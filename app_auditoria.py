import streamlit as st
import pandas as pd
import sqlite3, io, re, unicodedata, hashlib, os
from datetime import date

st.set_page_config(page_title="Auditoria EFS × Saúde", page_icon="🚛", layout="wide")
DB="auditoria_local.sqlite3"

def norm(x):
    return "".join(c for c in unicodedata.normalize("NFKD", str(x)) if not unicodedata.combining(c)).upper().strip()
def plate(x):
    return re.sub(r"[^A-Z0-9]", "", norm(x)) if pd.notna(x) else ""
def col(df,*names):
    for n in names:
        for c in df.columns:
            if norm(c)==norm(n): return c
    return None
def read(f):
    raw=f.getvalue()
    if f.name.lower().endswith((".xlsx",".xlsm")):
        return pd.read_excel(io.BytesIO(raw),dtype=str,engine="openpyxl").rename(columns=lambda c:str(c).strip())
    for enc in ("utf-8-sig","cp1252","latin1"):
        for sep in (";",",","\\t"):
            try:
                d=pd.read_csv(io.BytesIO(raw),encoding=enc,sep=sep,dtype=str,low_memory=False)
                if len(d.columns)>2: return d.rename(columns=lambda c:str(c).strip())
            except Exception: pass
    raise ValueError(f"Não foi possível ler {f.name}")
def status(x):
    n=norm(x)
    if "SAUD" in n:return "Saudável"
    if "AVAL" in n:return "Avalie"
    if "ATEN" in n:return "Atenção"
    return "Sem informação"
def grade(x): return {"Atenção":0,"Avalie":1,"Saudável":2}.get(x)
def rota(x):
    return any(t in norm(x) for t in ("ROTA TRANSP","CIDADE SOL","GRUPO BRASILEIRO","BRASILEIRO ROTA","VIACAO GQE","AVIACAO GQE","GQE"))
def init():
    with sqlite3.connect(DB) as c:
        c.execute("CREATE TABLE IF NOT EXISTS servicos (chave TEXT PRIMARY KEY, placa TEXT, cliente TEXT, os TEXT, data TEXT, tipo TEXT, classe TEXT, status TEXT, oficina TEXT, origem TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS saude (data_base TEXT, placa TEXT, status TEXT, origem TEXT, PRIMARY KEY(data_base,placa))")
        c.execute("CREATE TABLE IF NOT EXISTS fotografias (data_base TEXT PRIMARY KEY, cobertura TEXT NOT NULL, arquivo TEXT, importado_em TEXT DEFAULT CURRENT_TIMESTAMP)")
def import_efs(files):
    inserted=0; skipped=0
    with sqlite3.connect(DB) as c:
        for f in files:
            d=read(f); p=col(d,"Placa"); cli=col(d,"Cliente"); typ=col(d,"Tipo de Atividade"); sta=col(d,"Status da Atividade")
            if not all((p,cli,typ,sta)):raise ValueError(f"{f.name}: faltam colunas EFS (Placa, Cliente, Tipo de Atividade, Status da Atividade)")
            oscol=col(d,"OS","Protocolo"); idcol=col(d,"ID da Atividade"); dt=col(d,"Data","Data de Conclusão","Data de Execução"); of=col(d,"Oficina")
            if not dt: raise ValueError(f"{f.name}: coluna de data não identificada; necessário para a cronologia")
            for _,r in d.iterrows():
                pl=plate(r[p]); s=norm(r[sta])
                if not pl or "CONCLUID" not in s or "NAO CONCLUID" in s: continue
                data=pd.to_datetime(r[dt],dayfirst=True,errors="coerce")
                if pd.isna(data):skipped+=1;continue
                typv=str(r[typ]); classe="Manutenção" if "MANUTEN" in norm(typv) else ("Instalação" if "INSTAL" in norm(typv) else "Outros")
                oid=str(r[oscol]).strip() if oscol and pd.notna(r[oscol]) else ""
                aid=str(r[idcol]).strip() if idcol and pd.notna(r[idcol]) else ""
                key=("OS:"+oid if oid else "ID:"+aid if aid else "HASH:"+hashlib.sha256((pl+str(r[cli])+typv+data.isoformat()).encode()).hexdigest())
                c.execute("INSERT OR REPLACE INTO servicos VALUES (?,?,?,?,?,?,?,?,?,?)",(key,pl,str(r[cli]),oid,data.isoformat(),typv,classe,str(r[sta]),str(r[of]) if of else "",f.name))
                inserted+=1
    return inserted,skipped
def import_health(f,day,coverage):
    d=read(f); p=col(d,"PLACA"); s=col(d,"STATUS","STATUS SAUDE","SAÚDE","SAUDE","SITUAÇÃO")
    if not p or not s:raise ValueError(f"{f.name}: precisa conter PLACA e STATUS")
    records={}
    for _,r in d.iterrows():
        pl=plate(r[p])
        if pl:records[pl]=status(r[s])
    with sqlite3.connect(DB) as c:
        # Reimportação de uma fotografia substitui completamente a fotografia anterior, sem sobras.
        c.execute("DELETE FROM saude WHERE data_base=?",(str(day),))
        c.executemany("INSERT INTO saude VALUES (?,?,?,?)",[(str(day),p,v,f.name) for p,v in records.items()])
        c.execute("INSERT OR REPLACE INTO fotografias(data_base,cobertura,arquivo) VALUES (?,?,?)",(str(day),coverage,f.name))
    return len(records)
def evolve(a,b):
    x,y=grade(a),grade(b)
    if x is None or y is None:return "Sem comparação confirmada"
    return "Melhorou" if y>x else "Piorou" if y<x else "Permaneceu igual"
def csv_bytes(d):return d.to_csv(index=False,sep=";").encode("utf-8-sig")
init()
st.title("🚛 Auditoria cronológica EFS × Saúde da frota")
st.caption("Fotografias sucessivas + OS concluídas entre elas. Melhorias após serviço são associações temporais, não prova de causalidade.")
with st.sidebar:
    st.header("1 · Importar")
    f_health=st.file_uploader("Fotografia de saúde (CSV ou XLSX)",type=["csv","xlsx"],key="health")
    day=st.date_input("Data da fotografia",value=date.today())
    coverage=st.radio("Abrangência do arquivo",["Completa (toda a frota)","Filtrada (somente alertas ou parte da frota)"],index=0)
    if st.button("Salvar fotografia",disabled=f_health is None,use_container_width=True):
        try:
            n=import_health(f_health,day,"completa" if coverage.startswith("Completa") else "filtrada")
            st.success(f"Fotografia de {day}: {n} placas gravadas.")
            st.rerun()
        except Exception as e:st.error(str(e))
    fs=st.file_uploader("EFS — vários arquivos CSV ou XLSX",type=["csv","xlsx"],accept_multiple_files=True,key="efs")
    if st.button("Salvar OS concluídas",disabled=not fs,use_container_width=True):
        try:
            n,bad=import_efs(fs)
            st.success(f"{n} linhas de OS processadas (repetições substituídas); {bad} sem data válida ignoradas.")
            st.rerun()
        except Exception as e:st.error(str(e))
    st.divider()
    st.header("2 · Gerenciar banco")
    with sqlite3.connect(DB) as c:
        nos=c.execute("SELECT COUNT(*) FROM servicos").fetchone()[0]
        nh=c.execute("SELECT COUNT(*) FROM saude").fetchone()[0]
    st.caption(f"{nos} OS únicas · {nh} registros de saúde")
    # Backup consistente com conexão SQLite (não apenas cópia do arquivo durante gravações).
    mem=io.BytesIO()
    with sqlite3.connect(DB) as source:
        temp=DB+".backup"
        with sqlite3.connect(temp) as dest:source.backup(dest)
    with open(temp,"rb") as f: backup_bytes=f.read()
    st.download_button("⬇️ Baixar backup SQLite",backup_bytes,"auditoria_local.sqlite3","application/octet-stream",use_container_width=True)
    restore=st.file_uploader("Restaurar backup (.sqlite3/.db)",type=["sqlite3","db"])
    if st.button("Restaurar backup",disabled=restore is None,use_container_width=True):
        try:
            b=restore.getvalue()
            if not b.startswith(b"SQLite format 3\\x00"):raise ValueError("Não é SQLite")
            temp=DB+".restore"
            with open(temp,"wb") as f:f.write(b)
            with sqlite3.connect(temp) as c:
                if c.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("Backup corrompido")
                tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"servicos","saude"}.issubset(tables):raise ValueError("Backup incompatível")
            os.replace(temp,DB)
            init()
            st.success("Backup restaurado")
            st.rerun()
        except Exception as e:st.error(str(e))
    st.caption("Streamlit Community Cloud: armazenamento SQLite local pode ser perdido ao reiniciar. Baixe backups após importações.")
    sure=st.checkbox("Confirmo a exclusão de todos os dados")
    typed=st.text_input("Digite ZERAR para confirmar")
    if st.button("🗑️ Zerar banco",disabled=not(sure and typed=="ZERAR"),use_container_width=True):
        with sqlite3.connect(DB) as c:
            c.execute("DELETE FROM servicos");c.execute("DELETE FROM saude");c.execute("DELETE FROM fotografias")
        st.rerun()
with sqlite3.connect(DB) as c:
    photos=pd.read_sql_query("SELECT data_base,cobertura,arquivo FROM fotografias ORDER BY data_base",c)
    health=pd.read_sql_query("SELECT * FROM saude",c)
    services=pd.read_sql_query("SELECT * FROM servicos",c)
if not health.empty and photos.empty:
    st.warning("Fotografias antigas sem abrangência declarada foram consideradas filtradas até confirmação.")
    with sqlite3.connect(DB) as c:
        for d in health.data_base.unique():
            c.execute("INSERT OR IGNORE INTO fotografias(data_base,cobertura,arquivo) VALUES (?,?,?)",(d,"filtrada","importação anterior"))
    with sqlite3.connect(DB) as c:photos=pd.read_sql_query("SELECT data_base,cobertura,arquivo FROM fotografias ORDER BY data_base",c)
st.subheader("📚 Fotografias registradas")
if photos.empty:
    st.info("Importe a primeira fotografia, marcando se o arquivo é completo ou filtrado.")
    st.stop()
pcount=health.groupby("data_base").agg(placas=("placa","nunique")).reset_index()
st.dataframe(photos.merge(pcount,on="data_base",how="left").fillna({"placas":0}),hide_index=True,use_container_width=True)
dates=photos.data_base.tolist()
if len(dates)<2:
    st.info("Importe a segunda fotografia para habilitar a comparação cronológica.")
    st.stop()
with st.sidebar:
    st.header("3 · Análise")
    end=st.selectbox("Fotografia final",dates,index=len(dates)-1)
    before=[d for d in dates if d<end]
    start=st.selectbox("Fotografia inicial",before,index=len(before)-1) if before else None
    only_rota=st.checkbox("Somente OS do Grupo Rota",value=True)
    extra=st.text_input("Outros nomes do grupo (separados por ;)")
    selected=st.multiselect("Tipos de OS",["Manutenção","Instalação","Outros"],default=["Manutenção","Instalação"])
if not start:
    st.info("Escolha uma fotografia final posterior à inicial.")
    st.stop()
subset=photos[(photos.data_base>=start)&(photos.data_base<=end)].copy()
days=subset.data_base.tolist()
health["categoria"]=health.status.map(status)
health_by_day={d:health[health.data_base==d].drop_duplicates("placa").set_index("placa").categoria.to_dict() for d in days}
coverage_by_day=dict(zip(photos.data_base,photos.cobertura))
# A ausência em arquivo filtrado é DESCONHECIDA; ausência em completo = não localizado na frota.
def health_value(day,pl):
    return health_by_day[day].get(pl,"Não localizado" if coverage_by_day[day]=="completa" else "Sem informação (base filtrada)")
if not services.empty:
    services["dt"]=pd.to_datetime(services.data,errors="coerce")
    terms=[norm(v) for v in extra.split(";") if v.strip()]
    if only_rota:services=services[services.cliente.map(lambda x:rota(x) or any(t in norm(x) for t in terms))]
    services=services[services.classe.isin(selected)]
    services=services[services.dt.notna()].copy()
else:services["dt"]=pd.Series(dtype="datetime64[ns]")
# Datas sem hora: OS no dia da fotografia não pode ser ordenada com segurança.
def interval_os(a,b):
    low=pd.Timestamp(a); high=pd.Timestamp(b)
    return services[(services.dt.dt.normalize()>low)&(services.dt.dt.normalize()<high)].copy()
def edge_os(a,b):
    low=pd.Timestamp(a); high=pd.Timestamp(b)
    return services[(services.dt.dt.normalize()==low)|(services.dt.dt.normalize()==high)].copy()
st.subheader(f"📈 Evolução da frota: {start} → {end}")
if any(coverage_by_day[d]!="completa" for d in days):
    st.warning("Há fotografias filtradas neste período. Ausência de placa nelas NÃO significa veículo saudável nem recuperado. Indicadores de frota total só são válidos para fotografias completas.")
fleet=[]
for d in days:
    snap=health[health.data_base==d]
    counts=snap.categoria.value_counts()
    fleet.append({"Fotografia":d,"Cobertura":coverage_by_day[d],"Placas presentes":snap.placa.nunique(),"Saudável":int(counts.get("Saudável",0)),"Avalie":int(counts.get("Avalie",0)),"Atenção":int(counts.get("Atenção",0))})
st.dataframe(pd.DataFrame(fleet),hide_index=True,use_container_width=True)
st.caption("Contagens de fotografias filtradas representam apenas placas presentes no arquivo, não o saldo total da frota.")
all_plates=set()
for d in days:all_plates.update(health_by_day[d])
all_plates.update(services[(services.dt.dt.normalize()>=pd.Timestamp(start))&(services.dt.dt.normalize()<=pd.Timestamp(end))].placa)
matrix=[]
for pl in sorted(all_plates):
    row={"Placa":pl}
    for d in days:row[d]=health_value(d,pl)
    row["Evolução inicial → final"]=evolve(row[start],row[end])
    matrix.append(row)
timeline=pd.DataFrame(matrix)
st.subheader("🚛 Evolução individual por placa")
st.dataframe(timeline,hide_index=True,use_container_width=True)
st.download_button("⬇️ Baixar evolução por placa",csv_bytes(timeline),"evolucao_frota.csv","text/csv")
st.subheader("🔧 Efetividade entre fotografias consecutivas")
results=[];details=[];boundary=[]
for a,b in zip(days[:-1],days[1:]):
    jobs=interval_os(a,b)
    ambiguous=edge_os(a,b)
    if not ambiguous.empty:
        boundary.append({"Intervalo":f"{a} → {b}","OS em datas-limite (ordem incerta)":len(ambiguous)})
    plates_jobs=set(jobs.placa)
    plates_all=set(health_by_day[a])|set(health_by_day[b])|plates_jobs
    for pl in sorted(plates_all):
        x=health_value(a,pl);y=health_value(b,pl)
        pj=jobs[jobs.placa==pl]
        change=evolve(x,y)
        if pl in plates_jobs:
            if change=="Melhorou":outcome="Melhorou após serviço (associação)"
            elif change=="Piorou":outcome="Piorou após serviço (investigar)"
            elif change=="Permaneceu igual" and x in ("Avalie","Atenção"):outcome="Permaneceu em alerta após serviço"
            elif change=="Permaneceu igual":outcome="Permaneceu igual após serviço"
            else:outcome="Sem comparação confirmada"
        else:outcome="Sem OS no intervalo"
        details.append({"Início":a,"Fim":b,"Placa":pl,"Antes":x,"Depois":y,"Evolução":change,"OS concluídas":len(pj),"Manutenções":int((pj.classe=="Manutenção").sum()),"Instalações":int((pj.classe=="Instalação").sum()),"Protocolos":", ".join(pj.os.fillna("").astype(str).unique()),"Avaliação":outcome})
    for _,r in jobs.iterrows():
        x=health_value(a,r.placa);y=health_value(b,r.placa)
        results.append({"Início":a,"Fim":b,"Data OS":r["dt"],"OS":r.os,"Placa":r.placa,"Cliente":r.cliente,"Tipo":r.classe,"Serviço":r.tipo,"Oficina":r.oficina,"Saúde antes":x,"Saúde depois":y,"Evolução":evolve(x,y)})
detail=pd.DataFrame(details);job_detail=pd.DataFrame(results)
if not detail.empty:
    k=detail[detail["OS concluídas"]>0]
    c1,c2,c3,c4=st.columns(4)
    c1.metric("Placas com OS entre fotografias",k.Placa.nunique())
    c2.metric("Melhoraram após OS",int((k.Avaliação=="Melhorou após serviço (associação)").sum()))
    c3.metric("Continuam em alerta",int((k.Avaliação=="Permaneceu em alerta após serviço").sum()))
    c4.metric("Pioraram após OS",int((k.Avaliação=="Piorou após serviço (investigar)").sum()))
    st.dataframe(detail,hide_index=True,use_container_width=True)
    st.download_button("⬇️ Baixar auditoria por placa",csv_bytes(detail),"auditoria_por_placa.csv","text/csv")
if not job_detail.empty:
    st.subheader("📋 OS concluídas vinculadas a cada intervalo")
    st.dataframe(job_detail,hide_index=True,use_container_width=True)
    st.download_button("⬇️ Baixar auditoria por OS",csv_bytes(job_detail),"auditoria_por_os.csv","text/csv")
else:st.info("Não há OS concluídas estritamente entre as datas das fotografias selecionadas.")
if boundary:
    st.warning("Existem OS no mesmo dia de uma fotografia. Sem horário confiável de extração, elas não entram no cálculo de efetividade, para evitar conclusões incorretas.")
    st.dataframe(pd.DataFrame(boundary),hide_index=True,use_container_width=True)
st.caption("Para medir evolução da frota inteira, use fotografias completas semanais. As fotografias filtradas de 27/10 e 02/11 são preservadas como histórico parcial. Uma melhoria observada após OS não demonstra, por si só, que a OS causou a melhoria.")
