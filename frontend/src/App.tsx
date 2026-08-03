import {
  CloudDoneOutlined, DashboardOutlined, EuroOutlined,
  FileUploadOutlined, ListAltOutlined, SettingsOutlined, WarningAmberOutlined,
} from "@mui/icons-material";
import {
  Alert, AppBar, Box, Button, Card, CardContent, CircularProgress, Container,
  Dialog, DialogActions, DialogContent, DialogTitle, Drawer, FormControlLabel,
  Grid, List, ListItemButton, ListItemIcon, ListItemText, Paper, Switch,
  Table, TableBody, TableCell, TableContainer, TableHead, TablePagination, TableRow, TextField,
  Toolbar, Typography,
} from "@mui/material";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { apiUrl, request, type Dashboard, type Page } from "./api";
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
  ["Steuerübersicht", "/steuer", <EuroOutlined />], ["FIFO-Zuordnungen", "/fifo", <ListAltOutlined />],
  ["Bestände", "/bestaende", <ListAltOutlined />], ["Steuerjournal", "/steuerjournal", <ListAltOutlined />],
  ["Exporte", "/exporte", <FileUploadOutlined />],
  ["Kraken API", "/kraken-api", <CloudDoneOutlined />],
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
  return <><Typography variant="h4" component="h1" gutterBottom>Übersicht</Typography><State {...state}>{state.data && <>{isEmptyDashboard(state.data)&&<Alert severity="info" sx={{mb:2}}>Noch keine Daten importiert. Beginnen Sie auf der Seite „Importe“.</Alert>}<Cards data={state.data} /><Paper sx={{ mt: 3, p: 3 }}><Typography variant="h6">Verarbeitungsweg</Typography><Typography sx={{ mt: 1 }}>CSV importiert → Rohdaten validiert → Fachliche Vorgänge erzeugt → EUR-Bewertung → FIFO und Steuerjournal</Typography></Paper></>}</State></>;
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
  const [page,setPage]=useState(0); const [rowsPerPage,setRowsPerPage]=useState(10);
  async function openDetail(row:Row){if(!detailPath)return;setDetailError("");try{setDetail(await request<Row>(detailPath(row)))}catch(error){setDetailError((error as Error).message)}}
  const rows=state.data?.items.slice(page*rowsPerPage,page*rowsPerPage+rowsPerPage)??[];
  return <><Typography variant="h5" component="h2" gutterBottom>{title}</Typography><State {...state}>{state.data?.items.length ? <Paper><TableContainer><Table size="small"><TableHead><TableRow>{columns.map(c=><TableCell key={c}>{c.replaceAll("_"," ")}</TableCell>)}{detailPath&&<TableCell>Details</TableCell>}</TableRow></TableHead><TableBody>{rows.map((row,i)=><TableRow key={String(row.id??i)}>{columns.map(c=><TableCell key={c}>{displayValue(row[c])}</TableCell>)}{detailPath&&<TableCell><Button onClick={()=>void openDetail(row)} aria-label={`Details zu ${String(row.id)}`}>Öffnen</Button></TableCell>}</TableRow>)}</TableBody></Table></TableContainer><TablePagination component="div" count={state.data.items.length} page={page} onPageChange={(_,value)=>setPage(value)} rowsPerPage={rowsPerPage} onRowsPerPageChange={event=>{setRowsPerPage(Number(event.target.value));setPage(0)}} labelRowsPerPage="Zeilen pro Seite"/></Paper>:<Alert severity="info">Noch keine Einträge vorhanden.</Alert>}</State>{detailError&&<Alert severity="error">{detailError}</Alert>}<Dialog open={Boolean(detail)} onClose={()=>setDetail(undefined)} fullWidth maxWidth="md"><DialogTitle>Nachweisdetails</DialogTitle><DialogContent>{detail&&Object.entries(detail).map(([key,value])=><Box key={key} sx={{py:1,borderBottom:"1px solid",borderColor:"divider"}}><strong>{key.replaceAll("_"," ")}</strong><Box component={typeof value==="object"&&value!==null?"pre":"span"} sx={{display:"block",whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{typeof value==="object"&&value!==null?JSON.stringify(value,null,2):displayValue(value)}</Box></Box>)}</DialogContent><DialogActions><Button onClick={()=>setDetail(undefined)}>Schließen</Button></DialogActions></Dialog></>;
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
function TaxOverviewPage() {
  const [year,setYear]=useState(new Date().getUTCFullYear()); const summary=useApi<Row>(`/api/tax-summary?year=${year}`);
  const runs=useApi<Page<Row>>(`/api/tax-calculations?year=${year}`); const [notice,setNotice]=useState("");
  async function calculate(){try{const result=await request<Row>("/api/tax-calculations",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({year})});setNotice(`Berechnung ${String(result.status)}: ${String(result.allocations)} FIFO-Zuordnungen.`);runs.reload();summary.reload()}catch(error){setNotice((error as Error).message)}}
  const keys=["realized_gains","realized_losses","net_result","earn_inflows","fees","open_reviews","open_valuations","incomplete_disposals"];
  return <><Box sx={{display:"flex",gap:2,alignItems:"center",mb:3}}><Typography variant="h4" component="h1" sx={{flexGrow:1}}>Steuerübersicht</Typography><TextField label="Steuerjahr" type="number" value={year} onChange={event=>setYear(Number(event.target.value))}/><Button variant="contained" onClick={()=>void calculate()}>Berechnung starten</Button></Box>{notice&&<Alert sx={{mb:2}}>{notice}</Alert>}<State {...summary}>{summary.data&&<Grid container spacing={2}>{keys.map(key=><Grid key={key} size={{xs:12,sm:6,md:3}}><Card variant="outlined"><CardContent><Typography color="text.secondary">{key.replaceAll("_"," ")}</Typography><Typography variant="h5">{displayValue(summary.data?.[key])}</Typography></CardContent></Card></Grid>)}</Grid>}</State><Box sx={{mt:4}}><DataTable title="Berechnungsläufe" state={runs} columns={["period_start","period_end","status","allocations","journal_entries","reviews"]} detailPath={row=>`/api/tax-calculations/${String(row.id)}`}/></Box></>;
}
function FifoPage(){const state=useApi<Page<Row>>("/api/lot-allocations");return <><Typography variant="h4" component="h1" gutterBottom>FIFO-Zuordnungen</Typography><DataTable title="Loszuordnungen" state={state} columns={["disposed_at","asset","quantity","acquisition_cost_eur","proceeds_eur","fees_eur","gain_loss_eur","holding_seconds"]} detailPath={row=>`/api/lot-allocations/${String(row.id)}`}/></>}
function InventoryPage(){const state=useApi<Page<Row>>("/api/inventory-lots");return <><Typography variant="h4" component="h1" gutterBottom>Bestände</Typography><DataTable title="Verbleibende Erwerbslose" state={state} columns={["acquired_at","asset","original_quantity","remaining_quantity","remaining_cost_eur","rule_version"]} detailPath={row=>`/api/inventory-lots/${String(row.id)}`}/></>}
function JournalPage(){const state=useApi<Page<Row>>("/api/tax-journal");return <><Typography variant="h4" component="h1" gutterBottom>Steuerjournal</Typography><DataTable title="Unveränderliche Journaleinträge" state={state} columns={["occurred_at","tax_year","type","asset","quantity","eur_value","gain_loss_eur","status"]} detailPath={row=>`/api/tax-journal/${String(row.id)}`}/></>}
function ExportsPage(){const state=useApi<Page<Row>>("/api/exports");const runs=useApi<Page<Row>>("/api/tax-calculations");const [kind,setKind]=useState("tax_journal_csv");const [notice,setNotice]=useState("");async function create(){const run=runs.data?.items[0];if(!run){setNotice("Zuerst muss eine Steuerberechnung vorliegen.");return}try{await request("/api/exports",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({tax_calculation_run_id:run.id,kind})});setNotice("Export wurde erstellt.");state.reload()}catch(error){setNotice((error as Error).message)}}return <><Typography variant="h4" component="h1" gutterBottom>Exporte</Typography><Paper sx={{p:3,mb:3,display:"flex",gap:2}}><TextField select SelectProps={{native:true}} label="Exportformat" value={kind} onChange={event=>setKind(event.target.value)}><option value="tax_journal_csv">Steuerjournal CSV</option><option value="fifo_allocations_csv">FIFO CSV</option><option value="inventory_csv">Bestände CSV</option><option value="valuation_evidence_csv">Bewertungen CSV</option><option value="reviews_csv">Prüffälle CSV</option><option value="annual_summary_csv">Jahresübersicht CSV</option><option value="tax_report_pdf">Steuerbericht PDF</option></TextField><Button variant="contained" onClick={()=>void create()}>Export starten</Button></Paper>{notice&&<Alert sx={{mb:2}}>{notice}</Alert>}<DataTable title="Exportartefakte" state={state} columns={["created_at","kind","status","file_name","size_bytes"]}/>{state.data?.items.map(item=><Button key={String(item.id)} component="a" href={apiUrl(String(item.download_url))} sx={{mt:1,mr:1}}>Herunterladen: {String(item.kind)}</Button>)}</>}
function KrakenApiPage(){
  const connection=useApi<Row>("/api/kraken/connection");const [file,setFile]=useState<File>();const [start,setStart]=useState("");const [end,setEnd]=useState("");const [comparison,setComparison]=useState<Row>();const [confirmed,setConfirmed]=useState(false);const [notice,setNotice]=useState("");const [busy,setBusy]=useState(false);
  async function compare(){if(!file||!start||!end){setNotice("CSV, Beginn und Ende sind erforderlich.");return}setBusy(true);setNotice("");const form=new FormData();form.append("file",file);form.append("start",new Date(`${start}Z`).toISOString());form.append("end",new Date(`${end}Z`).toISOString());try{setComparison(await request<Row>("/api/kraken/ledger-compare",{method:"POST",body:form}));setConfirmed(false)}catch(error){setNotice((error as Error).message)}finally{setBusy(false)}}
  async function importLedger(){if(!comparison||comparison.ready_for_import!==true||!confirmed)return;setBusy(true);try{const result=await request<Row>("/api/kraken/ledger-import",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({start:new Date(`${start}Z`).toISOString(),end:new Date(`${end}Z`).toISOString(),expected_ledger_id_digest:comparison.api_ledger_id_digest,explicit_confirmation:true,transform:false})});setNotice(`Ledger importiert: ${String(result.created_records)} neu, ${String(result.reused_records)} wiederverwendet.`)}catch(error){setNotice((error as Error).message)}finally{setBusy(false)}}
  return <><Typography variant="h4" component="h1" gutterBottom>Kraken API</Typography><State {...connection}>{connection.data&&<Alert severity={connection.data.ledger_permission_available?"success":"warning"}>{String(connection.data.message)}</Alert>}</State><Paper sx={{p:3,my:3}}><Typography variant="h6">Ledger mit CSV abgleichen</Typography><Typography color="text.secondary" sx={{mb:2}}>Nur lesende Vorschau. Es werden noch keine Daten gespeichert.</Typography><TextField label="Beginn (UTC)" type="datetime-local" value={start} onChange={event=>{setStart(event.target.value);setComparison(undefined)}} slotProps={{inputLabel:{shrink:true}}} sx={{mr:2}}/><TextField label="Ende (UTC, exklusiv)" type="datetime-local" value={end} onChange={event=>{setEnd(event.target.value);setComparison(undefined)}} slotProps={{inputLabel:{shrink:true}}} sx={{mr:2}}/><Button component="label">Ledger-CSV wählen<input hidden type="file" accept=".csv,text/csv" onChange={event=>{setFile(event.target.files?.[0]);setComparison(undefined)}}/></Button><Button variant="contained" disabled={busy||!file||!start||!end} onClick={()=>void compare()}>Vergleichen</Button></Paper>{comparison&&<Paper sx={{p:3}}><Alert severity={comparison.ready_for_import?"success":"warning"}>{comparison.ready_for_import?"CSV und Live-Ledger stimmen überein.":"Der Vergleich enthält Abweichungen."}</Alert><Typography sx={{mt:2,overflowWrap:"anywhere"}}>Digest: {String(comparison.api_ledger_id_digest)}</Typography><Typography>Übereinstimmende IDs: {String(comparison.matched_ids)}</Typography><Typography>Feldabweichungen: {String(comparison.field_mismatch_count)}</Typography><FormControlLabel control={<Switch checked={confirmed} onChange={(_,value)=>setConfirmed(value)}/>} label="Ich bestätige diesen geprüften Digest ausdrücklich."/><Button variant="contained" disabled={busy||comparison.ready_for_import!==true||!confirmed} onClick={()=>void importLedger()}>Ledger jetzt importieren</Button></Paper>}{notice&&<Alert sx={{mt:2}} severity={notice.includes("importiert")?"success":"error"}>{notice}</Alert>}</>;
}
function SystemPage(){const s=useApi<Row>("/api/system/status");return <><Typography variant="h4" component="h1" gutterBottom>System</Typography><State {...s}>{s.data&&<Paper sx={{p:3}}>{safeSystemEntries(s.data).map(([k,v])=><Box key={k} sx={{display:"flex",gap:2,py:1,borderBottom:"1px solid",borderColor:"divider"}}><CloudDoneOutlined color={v?"success":"disabled"}/><strong>{k.replaceAll("_"," ")}</strong><span>{displayValue(v)}</span></Box>)}</Paper>}</State></>}
export function App() {
  const location=useLocation(); return <Box sx={{display:"flex",minHeight:"100vh"}}><AppBar position="fixed" sx={{zIndex:t=>t.zIndex.drawer+1}}><Toolbar><Typography variant="h6">Kraken Tax Companion</Typography></Toolbar></AppBar><Drawer variant="permanent" sx={{width:drawerWidth,flexShrink:0,"& .MuiDrawer-paper":{width:drawerWidth,boxSizing:"border-box"}}}><Toolbar/><List component="nav" aria-label="Hauptnavigation">{navigation.map(([label,path,icon])=><ListItemButton component={Link} key={path} selected={location.pathname===path} to={path}><ListItemIcon>{icon}</ListItemIcon><ListItemText primary={label}/></ListItemButton>)}</List></Drawer><Box component="main" sx={{flexGrow:1,pt:12,pb:6,minWidth:0}}><Container maxWidth="xl"><Routes><Route path="/" element={<DashboardPage/>}/><Route path="/importe" element={<ImportsPage/>}/><Route path="/vorgaenge" element={<EventsPage/>}/><Route path="/bewertungen" element={<ValuationsPage/>}/><Route path="/prueffaelle" element={<ReviewsPage/>}/><Route path="/steuer" element={<TaxOverviewPage/>}/><Route path="/fifo" element={<FifoPage/>}/><Route path="/bestaende" element={<InventoryPage/>}/><Route path="/steuerjournal" element={<JournalPage/>}/><Route path="/exporte" element={<ExportsPage/>}/><Route path="/kraken-api" element={<KrakenApiPage/>}/><Route path="/system" element={<SystemPage/>}/><Route path="*" element={<Navigate replace to="/"/>}/></Routes></Container></Box></Box>;
}
