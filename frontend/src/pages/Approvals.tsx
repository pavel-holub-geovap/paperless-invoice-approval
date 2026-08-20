import { useEffect, useState } from "react";
import { api, money } from "../lib/api";
import type { ApprovalTask } from "../types";
import { StatusBadge } from "../components/StatusBadge";

export function Approvals() {
  const [tasks,setTasks]=useState<ApprovalTask[]>([]); const [comments,setComments]=useState<Record<string,string>>({}); const [error,setError]=useState("");
  const load=()=>api<ApprovalTask[]>("/approvals/mine").then(setTasks).catch((e)=>setError(e.message));
  useEffect(()=>{void load();},[]);
  const decide=async(task:ApprovalTask,action:string)=>{try{await api(`/approvals/${task.id}/decision`,{method:"POST",body:JSON.stringify({action,comment:comments[task.id]||null})});setError("");load();}catch(e){setError((e as Error).message);}};
  return <section><div className="section-heading"><div><p className="eyebrow">Moje práce</p><h1>Schvalování</h1></div></div>{error&&<div className="alert danger">{error}</div>}<div className="task-grid">{tasks.filter(t=>t.current).map(task=><article className="card approval-card" key={task.id}><div className="card-title"><div><h2>{task.invoice_number||"Faktura"}</h2><p>{task.supplier_name}</p></div><StatusBadge value={task.invoice_status}/></div><div className="amount-block"><span>Faktura celkem<strong>{money(task.invoice_total,task.currency)}</strong></span><span>Schvaluji za {task.cost_center}<strong>{money(task.allocation_amount,task.currency)}</strong></span></div>{task.decision?<StatusBadge value={task.decision}/>:<><a className="button secondary" href={`/api/invoices/${task.invoice_id}/pdf`} target="_blank" rel="noreferrer">Zobrazit originál</a><textarea placeholder="Komentář je povinný pro vrácení a zamítnutí" value={comments[task.id]||""} onChange={e=>setComments({...comments,[task.id]:e.target.value})}/><div className="decision-buttons"><button className="button primary" onClick={()=>void decide(task,"APPROVE")}>Schválit</button><button className="button warning" onClick={()=>void decide(task,"RETURN")}>Vrátit</button><button className="button danger" onClick={()=>void decide(task,"REJECT")}>Zamítnout</button></div></>}</article>)}</div></section>;
}

