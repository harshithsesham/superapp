// Local visual fixture using the production component. No accounts or live stores.
import React from "react";
import { createRoot } from "react-dom/client";
import { GroceryScreen } from "../../src/GroceryScreen";
let order = { id: "basket", platform: "list", status: "draft", fingerprint: "preview",
  lines: [{ item_id: "milk", name: "Organic whole milk", quantity: 1, unit: "" }, { item_id: "coffee", name: "Coffee beans", quantity: 2, unit: "" }] };
const items = [
 { id: "milk", name: "Organic whole milk", category: "Dairy & Protein", status: "running_low", basis: "measured", reason: "Usually lasts about a week. Last bought 6 days ago." },
 { id: "coffee", name: "Coffee beans", category: "Beverages", status: "out", basis: "declared", reason: "You marked this as out." },
 { id: "apples", name: "Honeycrisp apples", category: "Fresh Produce", status: "stocked", basis: "estimated", reason: "Bought 2 days ago." }];
window.fetch = (async (url: string, opts: any = {}) => {
 const path = String(url).split("/v1/grocery")[1]; const body = opts.body ? JSON.parse(opts.body) : {};
 let data: any;
 if(path === "/state") data={mail_connected:true,item_count:items.length,pending_orders:[{id:order.id}],shelves:[...new Set(items.map(i=>i.category))].map(category=>({category,items:items.filter(i=>i.category===category)}))};
 else if(path === "/platforms") data={platforms:[{platform:"instacart",available:true,can_handoff:true}]};
 else if(path === "/orders/basket" && opts.method === "PATCH") {order={...order,...body,fingerprint:order.fingerprint+"x"};data={order};}
 else if(path === "/orders/basket") data=order;
 else if(path === "/items") {const id=body.name.toLowerCase();items.push({id,name:body.name,category:"Other items",status:"stocked",basis:"assumed",reason:"You added this."});data={item_id:id};}
 else if(path === "/basket") {for(const id of body.item_ids) if(!order.lines.some(l=>l.item_id===id)) order.lines.push({item_id:id,name:items.find(i=>i.id===id)!.name,quantity:1,unit:""});data={order};}
 else if(path?.endsWith("/out")) data={ok:true};
 else return {ok:false,json:async()=>({detail:"This visual preview doesn't contact a store. Your list is saved."})} as Response;
 return {ok:true,json:async()=>structuredClone(data)} as Response;
}) as typeof fetch;
createRoot(document.getElementById("root")!).render(<GroceryScreen apiUrl="" auth={{Authorization:"preview"}} onConnect={()=>{}} onAskNano={()=>{}}/>);
