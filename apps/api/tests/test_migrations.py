"""The migration chain is linear and actually runs.

This exists because a duplicate revision has now slipped through three times,
and every time the whole suite stayed green: the tests build their schema with
`create_all`, which never reads a revision id. A broken chain is invisible to
every other test in this repo and fatal on deploy — `alembic upgrade head`
refuses to pick between two heads and the API starts against a stale database.
"""
import pathlib
import re
import subprocess
import sys

from sqlalchemy import create_engine, inspect, text

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
API_ROOT = VERSIONS.parents[1]


def _revisions() -> dict[str, tuple[str, str | None]]:
    """{revision: (filename, down_revision)} for every migration on disk."""
    out = {}
    for path in sorted(VERSIONS.glob("[0-9]*.py")):
        text = path.read_text()
        rev = re.search(r'^revision = ["\'](\w+)["\']', text, re.M)
        down = re.search(r'^down_revision = (?:["\'](\w+)["\']|None)', text, re.M)
        assert rev, f"{path.name} declares no revision"
        assert rev.group(1) not in out, (
            f"revision {rev.group(1)} is declared by both {out.get(rev.group(1), ('?',))[0]} "
            f"and {path.name} — alembic cannot choose a head")
        out[rev.group(1)] = (path.name, down.group(1) if down else None)
    return out


def test_every_revision_id_is_unique():
    """Two files claiming one id is the exact failure that has recurred: it
    merges cleanly in git, passes every test, and breaks the deploy."""
    revs = _revisions()
    assert len(revs) >= 20


def test_the_chain_is_one_line_with_a_single_head():
    """Exactly one root, exactly one head, no forks and no orphans."""
    revs = _revisions()
    downs = [d for _, d in revs.values()]

    roots = [r for r, (_, d) in revs.items() if d is None]
    assert len(roots) == 1, f"expected one root migration, found {roots}"

    heads = [r for r in revs if r not in downs]
    assert len(heads) == 1, (
        f"multiple heads: {sorted(heads)} — `alembic upgrade head` cannot resolve this")

    missing = [d for d in downs if d is not None and d not in revs]
    assert not missing, f"revisions point at parents that do not exist: {missing}"

    forked = [d for d in set(downs) if d is not None and downs.count(d) > 1]
    assert not forked, f"more than one migration claims the same parent: {forked}"

    # And the chain reaches every file from the root, so nothing is stranded.
    by_down: dict[str | None, str] = {d: r for r, (_, d) in revs.items()}
    walked, cur = 0, roots[0]
    while cur is not None:
        walked += 1
        cur = by_down.get(cur)
    assert walked == len(revs), f"walked {walked} of {len(revs)} migrations from the root"


def test_alembic_upgrade_head_actually_runs(tmp_path):
    """The check no unit test can fake: run the real thing against a real
    empty database and require it to reach head."""
    db = tmp_path / "chain.db"
    env = {"PATH": "/usr/bin:/bin", "SUPERAPP_DATABASE_URL": f"sqlite:///{db}"}
    run = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                         cwd=API_ROOT, env=env, capture_output=True, text=True)
    assert run.returncode == 0, f"upgrade failed:\n{run.stdout}\n{run.stderr}"
    assert "FAILED" not in run.stdout + run.stderr, run.stdout + run.stderr

    cur = subprocess.run([sys.executable, "-m", "alembic", "current"],
                         cwd=API_ROOT, env=env, capture_output=True, text=True)
    assert "(head)" in cur.stdout, f"did not reach head:\n{cur.stdout}\n{cur.stderr}"


def test_current_main_database_upgrades_without_skipping_groceries(tmp_path):
    """A linear fresh chain can still reuse main's IDs for different tables.

    Start at main's published 0023 with a real mailbox row, then upgrade and
    check the schema, not merely the version stamp.
    """
    expected = {
        "0020": "0020_draft_generation.py",
        "0021": "0021_inbox_signals.py",
        "0022": "0022_memory_provenance.py",
        "0023": "0023_draft_imported_context.py",
    }
    revisions = _revisions()
    assert {rev: revisions[rev][0] for rev in expected} == expected
    url = f"sqlite:///{tmp_path / 'main-upgrade.db'}"
    env = {"PATH": "/usr/bin:/bin", "SUPERAPP_DATABASE_URL": url}

    def upgrade(target):
        run = subprocess.run([sys.executable, "-m", "alembic", "upgrade", target],
                             cwd=API_ROOT, env=env, capture_output=True, text=True)
        assert run.returncode == 0, run.stdout + run.stderr

    upgrade("0023")
    engine = create_engine(url)
    try:
        assert "grocery_items" not in inspect(engine).get_table_names()
        assert "used_imported_context" in {
            c["name"] for c in inspect(engine).get_columns("inbox_drafts")}
        with engine.begin() as db:
            db.execute(text("""INSERT INTO gmail_accounts
                (id,user_id,email,history_id,created_at) VALUES
                ('existing-account','existing-user','me@example.com','keep-cursor',CURRENT_TIMESTAMP)
            """))
        upgrade("head")
        upgrade("head")  # repeat deployment is inert
        tables = set(inspect(engine).get_table_names())
        assert {"grocery_items", "grocery_orders", "grocery_links", "grocery_purchases", "grocery_receipts", "saved_context"} <= tables
        assert {"recovery_state", "sync_error", "last_sync_at", "history_import_state"} <= {
            c["name"] for c in inspect(engine).get_columns("gmail_accounts")}
        with engine.connect() as db:
            assert db.scalar(text("SELECT history_id FROM gmail_accounts WHERE id='existing-account'")) == "keep-cursor"
            assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0029"
    finally:
        engine.dispose()
