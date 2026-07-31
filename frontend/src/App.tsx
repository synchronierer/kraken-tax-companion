import {
  CloudDoneOutlined, DashboardOutlined, EuroOutlined,
  FileUploadOutlined, ListAltOutlined, SettingsOutlined, WarningAmberOutlined,
} from "@mui/icons-material";
import {
  Alert, AppBar, Box, Button, Card, CardContent, CircularProgress, Container,
  Dialog, DialogActions, DialogContent, DialogTitle, Drawer, FormControlLabel,
  Grid, List, ListItemButton, ListItemIcon, ListItemText, Paper, Switch,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField,
  Toolbar, Typography,
} from "@mui/material";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { request, type Dashboard, type Page } from "./api";
import {
  displayValue,
  isEmptyDashboard,
  reviewGroup,
  safeSystemEntries,
  validateManualPrice,
} from "./ui-models";

const drawerWidth = 232;
const navigation = [
  ["Übersicht", "/", <DashboardOutlined />], ["Importe", "/importe", <FileUploadOutlined />],
  ["Vorgänge", "/vorgaenge", <ListAltOutlined />], ["Bewertungen", "/bewertungen", <EuroOutlined />],
  ["Prüffälle", "/prueffaelle", <WarningAmberOutlined />], ["System", "/system", <SettingsOutlined />],
] as const;
type Row = Record<string, string | number | boolean | null>;

function useApi<T>(path: string) {
  const [data, setData] = useState<T>(); const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const reload = () => { const controller = new AbortController(); setLoading(true);
    request<T>(path, {}, controller.signal).then(setData).catch((e: Error) => setError(e.message)).finally(() => setLoading(false));
    return () => controller.abort(); };
  useEffect(() => {
    const controller = new AbortController();
    request<T>(path, {}, controller.signal)
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [path]);
  return { data, error, loading, reload };
}
function State({ loading, error, children }: { loading: boolean; error: string; children: ReactNode }) {
  if (loading) return <Box sx={{ p: 5, textAlign: "center" }}><CircularProgress aria-label="Laden" /></Box>;
  if (error) return <Alert severity="error">{error}</Alert>; return <>{children}</>;
}
function Cards({ data }: { data: Dashboard }) {
  const items = [["Importierte Dateien", data.imports], ["Rohdatensätze", data.raw_records], ["Rewards", data.rewards],
    ["Trades", data.trades], ["Bewertete Vorgänge", data.resolved_valuations], ["Offene Bewertungen", data.open_valuations], ["Prüffälle", data.review_cases]];
  return <Grid container spacing={2}>{items.map(([label, value]) => <Grid key={label} size={{ xs: 12, sm: 6, md: 3 }}><Card variant="outlined"><CardContent><Typography color="text.secondary">{label}</Typography><Typography variant="h4">{value}</Typography></CardContent></Card></Grid>)}</Grid>;
}
function DashboardPage() {
  const state = useApi<Dashboard>("/api/dashboard");
  return <><Typography variant="h4" component="h1" gutterBottom>Übersicht</Typography><State {...state}>{state.data && <>{isEmptyDashboard(state.data)&&<Alert severity="info" sx={{mb:2}}>Noch keine Daten importiert. Beginnen Sie auf der Seite „Importe“.</Alert>}<Cards data={state.data} /><Paper sx={{ mt: 3, p: 3 }}><Typography variant="h6">Verarbeitungsweg</Typography><Typography sx={{ mt: 1 }}>CSV importiert → Rohdaten validiert → Fachliche Vorgänge erzeugt → EUR-Bewertung → Steuerjournal folgt in Sprint 3B</Typography></Paper></>}</State></>;
}
function ImportsPage() {
  const state = useApi<Page<Row>>("/api/imports"); const [direct, setDirect] = useState(true);
  const transformations = useApi<Page<Row>>("/api/transformations");
  const [result, setResult] = useState(""); const [busy, setBusy] = useState(false);
  async function upload(file: File) { setBusy(true); setResult(""); const form = new FormData(); form.append("file", file);
    try { const answer = await request<Row>(`/api/imports/kraken?transform=${direct}`, { method: "POST", body: form }); setResult(answer.duplicate ? "Duplikat erkannt – keine Rohdaten verdoppelt." : "Import abgeschlossen."); state.reload(); }
    catch (e) { setResult((e as Error).message); } finally { setBusy(false); } }
  return <><Typography variant="h4" component="h1">Importe</Typography><Paper sx={{ my: 3, p: 3, border: "2px dashed", borderColor: "divider" }}><Typography variant="h6">Kraken CSV hochladen</Typography><Typography color="text.secondary">Ledger History oder Trades History, ausschließlich .csv</Typography>
    <Button component="label" variant="contained" startIcon={<FileUploadOutlined />} sx={{ mt: 2 }} disabled={busy}>Datei auswählen<input hidden type="file" accept=".csv,text/csv" onChange={(e) => { const file=e.target.files?.[0]; if(file) void upload(file); }} /></Button>
    <FormControlLabel sx={{ ml: 2 }} control={<Switch checked={direct} onChange={(_, v) => setDirect(v)} />} label="Nach Import direkt verarbeiten" />{busy && <CircularProgress size={24} />}{result && <Alert sx={{ mt: 2 }} severity={result.includes("abgeschlossen") || result.includes("Duplikat") ? "success" : "error"}>{result}</Alert>}</Paper>
    <DataTable title="Importhistorie" state={state} columns={["source","status","records","hash","imported_at"]} detailPath={row=>`/api/imports/${String(row.id)}`} /><Box sx={{mt:4}}><DataTable title="Transformationsläufe" state={transformations} columns={["status","started_at","completed_at","checked","review_cases"]} detailPath={row=>`/api/transformations/${String(row.id)}`}/></Box></>;
}
function DataTable({ title, state, columns, detailPath }: { title: string; state: ReturnType<typeof useApi<Page<Row>>>; columns: string[]; detailPath?: (row: Row)=>string }) {
  const [detail,setDetail]=useState<Row>(); const [detailError,setDetailError]=useState("");
  async function openDetail(row:Row){if(!detailPath)return;setDetailError("");try{setDetail(await request<Row>(detailPath(row)))}catch(error){setDetailError((error as Error).message)}}
  return <><Typography variant="h5" component="h2" gutterBottom>{title}</Typography><State {...state}>{state.data?.items.length ? <TableContainer component={Paper}><Table size="small"><TableHead><TableRow>{columns.map(c=><TableCell key={c}>{c.replaceAll("_"," ")}</TableCell>)}{detailPath&&<TableCell>Details</TableCell>}</TableRow></TableHead><TableBody>{state.data.items.map((row,i)=><TableRow key={String(row.id??i)}>{columns.map(c=><TableCell key={c}>{displayValue(row[c])}</TableCell>)}{detailPath&&<TableCell><Button onClick={()=>void openDetail(row)} aria-label={`Details zu ${String(row.id)}`}>Öffnen</Button></TableCell>}</TableRow>)}</TableBody></Table></TableContainer>:<Alert severity="info">Noch keine Einträge vorhanden.</Alert>}</State>{detailError&&<Alert severity="error">{detailError}</Alert>}<Dialog open={Boolean(detail)} onClose={()=>setDetail(undefined)} fullWidth maxWidth="md"><DialogTitle>Nachweisdetails</DialogTitle><DialogContent>{detail&&Object.entries(detail).map(([key,value])=><Box key={key} sx={{py:1,borderBottom:"1px solid",borderColor:"divider"}}><strong>{key.replaceAll("_"," ")}</strong><Box component={typeof value==="object"&&value!==null?"pre":"span"} sx={{display:"block",whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{typeof value==="object"&&value!==null?JSON.stringify(value,null,2):displayValue(value)}</Box></Box>)}</DialogContent><DialogActions><Button onClick={()=>setDetail(undefined)}>Schließen</Button></DialogActions></Dialog></>;
}
function EventsPage() { const state=useApi<Page<Row>>("/api/events"); const type=(row:Row)=>String(row.type)==="Erwerb"?"acquisition":String(row.type)==="Veräußerung"?"disposal":String(row.type)==="Gebühr"?"fee":"trade"; return <><Typography variant="h4" component="h1" gutterBottom>Vorgänge</Typography><DataTable title="Fachliche Ereignisse" state={state} columns={["occurred_at","type","asset","quantity","valuation_status"]} detailPath={row=>`/api/events/${type(row)}/${String(row.id)}`}/></>; }
function ValuationsPage() {
  const requirements=useApi<Page<Row>>("/api/valuation-requirements"); const prices=useApi<Page<Row>>("/api/prices");
  const [open,setOpen]=useState(false); const [notice,setNotice]=useState("");
  async function run(){try{const x=await request<Row>("/api/valuations",{method:"POST"});setNotice(`Lauf ${x.status}: ${x.resolved} Bewertungen aufgelöst.`);requirements.reload();prices.reload();}catch(e){setNotice((e as Error).message)}}
  async function uploadPrices(file:File){const form=new FormData();form.append("file",file);try{const result=await request<Row>("/api/prices/manual/csv",{method:"POST",body:form});setNotice(`${String(result.count)} manuelle Tageskurse importiert.`);prices.reload()}catch(error){setNotice((error as Error).message)}}
  const decisions=useApi<Page<Row>>("/api/valuations");
  return <><Box sx={{display:"flex",justifyContent:"space-between"}}><Typography variant="h4" component="h1">Bewertungen</Typography><Box><Button onClick={()=>setOpen(true)}>Manueller Tageskurs</Button><Button component="label">Kurs-CSV hochladen<input hidden type="file" accept=".csv,text/csv" onChange={event=>{const file=event.target.files?.[0];if(file)void uploadPrices(file)}}/></Button><Button variant="contained" onClick={()=>void run()}>Automatische Bewertung starten</Button></Box></Box>{notice&&<Alert sx={{my:2}}>{notice}</Alert>}<DataTable title="Offene Anforderungen" state={requirements} columns={["asset","date","event_type","method","status"]}/><Box sx={{mt:4}}><DataTable title="Bewertungsentscheidungen" state={decisions} columns={["asset","date","method","eur_value","provider","status","version"]} detailPath={row=>`/api/valuations/${String(row.id)}`}/></Box><Box sx={{mt:4}}><DataTable title="Tageskurse" state={prices} columns={["asset","date","price_eur","method","source","samples","status","version"]} detailPath={row=>`/api/prices/${String(row.id)}`}/></Box><ManualDialog open={open} close={()=>setOpen(false)} done={()=>{setOpen(false);prices.reload()}}/></>;
}
function ManualDialog({open,close,done}:{open:boolean;close:()=>void;done:()=>void}) {
  const [error,setError]=useState(""); async function submit(e:FormEvent<HTMLFormElement>){e.preventDefault();const d=new FormData(e.currentTarget);const fields=Object.fromEntries(d) as {asset:string;date:string;price_eur:string;source:string;reason:string};const invalid=validateManualPrice(fields);if(invalid){setError(invalid);return}try{await request("/api/prices/manual",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(fields)});done();}catch(x){setError((x as Error).message)}}
  return <Dialog open={open} onClose={close}><form onSubmit={(e)=>void submit(e)}><DialogTitle>Manuellen Tageskurs erfassen</DialogTitle><DialogContent>{error&&<Alert severity="error">{error}</Alert>}{[["asset","Asset"],["date","UTC-Datum"],["price_eur","Kurs in EUR"],["source","Quelle"],["reason","Begründung"]].map(([name,label])=><TextField key={name} name={name} label={label} type={name==="date"?"date":"text"} required fullWidth margin="dense" slotProps={{inputLabel:{shrink:true}}}/>)}</DialogContent><DialogActions><Button onClick={close}>Abbrechen</Button><Button type="submit" variant="contained">Speichern</Button></DialogActions></form></Dialog>;
}
function ReviewsPage(){const s=useApi<Page<Row>>("/api/reviews");return <><Typography variant="h4" component="h1" gutterBottom>Prüffälle</Typography>{s.data?.items.map(item=><Typography key={String(item.id)} variant="caption" sx={{mr:2}}>{reviewGroup(String(item.code))}</Typography>)}<DataTable title="Transformation und Bewertung" state={s} columns={["occurred_at","kind","code","message"]} detailPath={row=>`/api/reviews/${String(row.id)}`}/></>}
function SystemPage(){const s=useApi<Row>("/api/system/status");return <><Typography variant="h4" component="h1" gutterBottom>System</Typography><State {...s}>{s.data&&<Paper sx={{p:3}}>{safeSystemEntries(s.data).map(([k,v])=><Box key={k} sx={{display:"flex",gap:2,py:1,borderBottom:"1px solid",borderColor:"divider"}}><CloudDoneOutlined color={v?"success":"disabled"}/><strong>{k.replaceAll("_"," ")}</strong><span>{displayValue(v)}</span></Box>)}</Paper>}</State></>}
export function App() {
  const location=useLocation(); return <Box sx={{display:"flex",minHeight:"100vh"}}><AppBar position="fixed" sx={{zIndex:t=>t.zIndex.drawer+1}}><Toolbar><Typography variant="h6">Kraken Tax Companion</Typography></Toolbar></AppBar><Drawer variant="permanent" sx={{width:drawerWidth,flexShrink:0,"& .MuiDrawer-paper":{width:drawerWidth,boxSizing:"border-box"}}}><Toolbar/><List component="nav" aria-label="Hauptnavigation">{navigation.map(([label,path,icon])=><ListItemButton component={Link} key={path} selected={location.pathname===path} to={path}><ListItemIcon>{icon}</ListItemIcon><ListItemText primary={label}/></ListItemButton>)}</List></Drawer><Box component="main" sx={{flexGrow:1,pt:12,pb:6,minWidth:0}}><Container maxWidth="xl"><Routes><Route path="/" element={<DashboardPage/>}/><Route path="/importe" element={<ImportsPage/>}/><Route path="/vorgaenge" element={<EventsPage/>}/><Route path="/bewertungen" element={<ValuationsPage/>}/><Route path="/prueffaelle" element={<ReviewsPage/>}/><Route path="/system" element={<SystemPage/>}/><Route path="*" element={<Navigate replace to="/"/>}/></Routes></Container></Box></Box>;
}
