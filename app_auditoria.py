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
        for sep in (";",",","\t"):
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
def classificar_servico(x):
    """Classificação exclusiva: retirada sempre excluída, inclusive em nomes compostos."""
    n=norm(x)
    if any(t in n for t in ("RETIRADA", "RETIRAR", "DESINSTAL", "REMOCAO", "REMOVER")):
        return None
    if "UPGRADE" in n or "UP-GRADE" in n or "UP GRADE" in n: return "Upgrade"
    if "REINSTAL" in n: return "Reinstalação"
    if "MANUTEN" in n: return "Manutenção"
    if "INSTAL" in n: return "Instalação"
    return None

def concluida(x):
    n=norm(x)
    return "CONCLUID" in n and not any(t in n for t in ("NAO CONCLUID", "N CONCLUID", "INCONCLUID"))

def rota(x):
    """Somente empresas confirmadas do Grupo Rota; sem variantes GQ/GQE."""
    n = norm(x)
    return any(t in n for t in (
        "ROTA -", "ROTA TRANSP", "ROTA TRANSPORT", "CIDADE SOL",
        "GRUPO BRASILEIRO", "BRASILEIRO ROTA", "EXPRESSO BRASILEIRO",
        "VIACAO JEQUIE"
    ))

def tipo_execucao(oficina):
    """Classifica pelo EXECUTOR (Oficina), nunca pela coluna Cliente Oficina Própria.
    Na dúvida, não atribui à rede terceira automaticamente.
    """
    n = norm(oficina)
    if not n or n in ("NAN", "NONE", "NULL", "-"):
        return "Não identificado"
    if n.startswith("ROTA -") or n.startswith("ROTA ") or any(t in n for t in (
        "CIDADE SOL", "VIACAO JEQUIE", "EXPRESSO BRASILEIRO",
        "GRUPO BRASILEIRO", "BRASILEIRO ROTA"
    )):
        return "Própria"
    terceiros = (
        "FILT", "RK AUTO", "LUCAS NEIVA", "FEIRA RASTREADORES",
        "HASTAG", "HASHTAG", "INTEGRASOM", "YESHUA", "CRISTIANO LIMA",
        "BRENO ARRUDA", "CARDOSO", "JUÁ", "JUA RASTREADORES",
        "ON TRACK", "ONTRACK", "JMP", "MANOEL PEREIRA",
        "JOAO JOSE", "LG2", "TOPSON", "PRO RACK", "LOPES RASTREADORES"
    )
    if any(t in n for t in terceiros):
        return "Terceira"
    return "Não identificado"

def init():
    with sqlite3.connect(DB) as c:
        c.execute("CREATE TABLE IF NOT EXISTS servicos (chave TEXT PRIMARY KEY, placa TEXT, cliente TEXT, os TEXT, data TEXT, tipo TEXT, classe TEXT, status TEXT, oficina TEXT, origem TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS saude (data_base TEXT, placa TEXT, status TEXT, origem TEXT, PRIMARY KEY(data_base,placa))")
        c.execute("CREATE TABLE IF NOT EXISTS operacao (data_base TEXT, placa TEXT, situacao TEXT, PRIMARY KEY(data_base,placa))")
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
                if not pl or not concluida(s): continue
                data=pd.to_datetime(r[dt],dayfirst=True,errors="coerce")
                if pd.isna(data):skipped+=1;continue
                typv=str(r[typ]); classe=classificar_servico(typv)
                if classe is None: continue
                oid=str(r[oscol]).strip() if oscol and pd.notna(r[oscol]) else ""
                aid=str(r[idcol]).strip() if idcol and pd.notna(r[idcol]) else ""
                key=("OS:"+oid if oid else "ID:"+aid if aid else "HASH:"+hashlib.sha256((pl+str(r[cli])+typv+data.isoformat()).encode()).hexdigest())
                c.execute("INSERT OR REPLACE INTO servicos VALUES (?,?,?,?,?,?,?,?,?,?)",(key,pl,str(r[cli]),oid,data.isoformat(),typv,classe,str(r[sta]),str(r[of]) if of else "",f.name))
                inserted+=1
    return inserted,skipped
def operacao_status(value):
    n=norm(value)
    if not n or n in ("NAN","NONE","NULL","-"): return "Não informado"
    if "SINISTR" in n: return "Sinistrado"
    if "CANCEL" in n: return "Cancelado"
    if "FORA DE OPER" in n or "INOPERANTE" in n: return "Fora de operação"
    if "EM MANUTEN" in n or n=="MANUTENCAO": return "Em manutenção"
    if "EM OPER" in n or n in ("OPERANDO","ATIVO","ATIVA","OPERACIONAL"): return "Em operação"
    return "Não informado"

def import_health(f,day,coverage):
    d=read(f); p=col(d,"PLACA"); s=col(d,"STATUS","STATUS SAUDE","SAÚDE","SAUDE","SITUAÇÃO")
    if not p or not s:raise ValueError(f"{f.name}: precisa conter PLACA e STATUS")
    # Situação operacional é independente do status de saúde.
    opcol=col(d,"SITUAÇÃO","SITUACAO","SITUAÇÃO OPERACIONAL","SITUACAO OPERACIONAL","STATUS OPERACIONAL","STATUS DO VEÍCULO","STATUS DO VEICULO","SITUAÇÃO DO VEÍCULO","SITUACAO DO VEICULO","OPERAÇÃO","OPERACAO")
    records={}; operations={}
    for _,r in d.iterrows():
        pl=plate(r[p])
        if pl:
            records[pl]=status(r[s])
            operations[pl]=operacao_status(r[opcol]) if opcol else "Não informado"
    with sqlite3.connect(DB) as c:
        # Reimportação de uma fotografia substitui completamente a fotografia anterior, sem sobras.
        c.execute("DELETE FROM saude WHERE data_base=?",(str(day),))
        c.execute("DELETE FROM operacao WHERE data_base=?",(str(day),))
        c.executemany("INSERT INTO saude VALUES (?,?,?,?)",[(str(day),p,v,f.name) for p,v in records.items()])
        c.executemany("INSERT INTO operacao VALUES (?,?,?)",[(str(day),p,v) for p,v in operations.items()])
        c.execute("INSERT OR REPLACE INTO fotografias(data_base,cobertura,arquivo) VALUES (?,?,?)",(str(day),coverage,f.name))
    return len(records)
def evolve(a,b):
    x,y=grade(a),grade(b)
    if x is None or y is None:return "Sem comparação confirmada"
    return "Melhorou" if y>x else "Piorou" if y<x else "Permaneceu igual"
def csv_bytes(d):return d.to_csv(index=False,sep=";").encode("utf-8-sig")
init()
st.title("🚛 Grupo Rota | Visão executiva")
st.caption("1. Saúde e operação da frota  •  2. Resultado dos serviços concluídos")
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
    st.caption("Para incluir upgrades antigos, reimporte os CSVs EFS históricos: versões anteriores não gravavam essas OS.")
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
            if not b.startswith(b"SQLite format 3\x00"):raise ValueError("Não é SQLite")
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
            c.execute("DELETE FROM servicos");c.execute("DELETE FROM saude");c.execute("DELETE FROM operacao");c.execute("DELETE FROM fotografias")
        st.rerun()
with sqlite3.connect(DB) as c:
    photos=pd.read_sql_query("SELECT data_base,cobertura,arquivo FROM fotografias ORDER BY data_base",c)
    health=pd.read_sql_query("SELECT * FROM saude",c)
    services=pd.read_sql_query("SELECT * FROM servicos",c)
    operation=pd.read_sql_query("SELECT * FROM operacao",c)
if not health.empty and photos.empty:
    st.warning("Fotografias antigas sem abrangência declarada foram consideradas filtradas até confirmação.")
    with sqlite3.connect(DB) as c:
        for d in health.data_base.unique():
            c.execute("INSERT OR IGNORE INTO fotografias(data_base,cobertura,arquivo) VALUES (?,?,?)",(d,"filtrada","importação anterior"))
    with sqlite3.connect(DB) as c:photos=pd.read_sql_query("SELECT data_base,cobertura,arquivo FROM fotografias ORDER BY data_base",c)

# Painel executivo: uma fotografia atual basta; histórico anterior é opcional.
if photos.empty:
    st.info("Importe uma fotografia de saúde para começar. Você pode importar as OS antes ou depois.")
    st.stop()
photos=photos.sort_values("data_base")
dates=photos.data_base.tolist()
with st.sidebar:
    st.header("3 · Resultado")
    end=st.selectbox("Fotografia atual",dates,index=len(dates)-1)
    first=date.fromisoformat(dates[0])
    start=st.date_input("Serviços executados a partir de",value=first,max_value=date.fromisoformat(end),key="period_start")
    selected=st.multiselect("Tipos de serviço",["Manutenção","Instalação","Reinstalação","Upgrade"],default=["Manutenção"],key="service_types")
    only_rota=st.checkbox("Somente Grupo Rota",value=True)
    extra=st.text_input("Outros nomes do Grupo Rota (separados por ;)")
    exec_filter=st.selectbox("Tipo de execução",["Geral","Própria","Terceira","Não identificado"],key="exec_filter")
    repeat_filter=st.selectbox("Reincidência de serviços",["Todos os serviços","Própria → Terceira","Terceira → Própria","Mesma modalidade"],key="repeat_filter")
    st.caption("As datas inicial e final são inclusivas. A fotografia final deve ter sido extraída depois dos serviços para confirmar o estado posterior.")

snap=health[health.data_base==end].drop_duplicates("placa").copy()
snap["estado"]=snap.status.map(status)
ops=operation[operation.data_base==end][["placa","situacao"]].drop_duplicates("placa") if not operation.empty else pd.DataFrame(columns=["placa","situacao"])
snap=snap.merge(ops,on="placa",how="left")
snap["situacao"]=snap.situacao.fillna("Não informado")
current_op=dict(zip(snap.placa,snap.situacao))
current=dict(zip(snap.placa,snap.estado))
coverage=dict(zip(photos.data_base,photos.cobertura))
if not services.empty:
    services["dt"]=pd.to_datetime(services.data,errors="coerce")
    services["classe"]=services.tipo.map(classificar_servico)
    services=services[services.classe.isin(selected)&services.dt.notna()].copy()
    terms=[norm(x) for x in extra.split(";") if x.strip()]
    if only_rota:
        services=services[services.cliente.map(lambda x:rota(x) or any(t in norm(x) for t in terms))]
    services=services[(services.dt.dt.date>=start)&(services.dt.dt.date<=date.fromisoformat(end))].copy()
    services=services[services.status.map(concluida)].copy()
    if "oficina" not in services.columns:
            services["oficina"] = ""
        services["Tipo de execução"] = services["oficina"].map(tipo_execucao)
else:
    services=pd.DataFrame(columns=["placa","dt","os","classe","cliente","oficina","tipo","Tipo de execução"])

def placar(label, value, bg, fg="#FFFFFF"):
    """Card visual. A filtragem detalhada continua nos controles abaixo."""
    import html
    label=html.escape(str(label))
    value=html.escape(f"{value:,}".replace(",", "."))
    st.markdown(f"""<div style="background:{bg};color:{fg};border-radius:13px;padding:16px 14px;min-height:108px;display:flex;flex-direction:column;justify-content:center;box-shadow:0 1px 4px rgba(0,0,0,.09)">
    <div style="font-size:clamp(22px,2.8vw,34px);font-weight:750;line-height:1.15">{value}</div>
    <div style="font-size:13px;font-weight:600;margin-top:8px;line-height:1.25">{label}</div></div>""",unsafe_allow_html=True)

st.subheader("1 · Fotografia atual da frota")
st.caption(f"Fotografia de {date.fromisoformat(end).strftime('%d/%m/%Y')} · {coverage.get(end,'desconhecida').capitalize()}")
if coverage.get(end)!="completa":
    st.warning("Fotografia filtrada: os números representam somente as placas do arquivo, não a frota total.")
placar("Veículos do cliente · fotografia atual",len(snap),"#165DAA")
op_categories=["Em operação","Fora de operação","Em manutenção","Cancelado","Sinistrado","Não informado"]
op_counts={x:int((snap.situacao==x).sum()) for x in op_categories}
columns=st.columns(3)
op_colors={"Em operação":("#B8DEFF","#123C66"),"Fora de operação":("#FFD6A1","#6E3C00"),"Em manutenção":("#E5E7EB","#252B36"),"Cancelado":("#C83E45","#FFFFFF"),"Sinistrado":("#6B7280","#FFFFFF"),"Não informado":("#EEF0F3","#394150")}
for i,cat in enumerate(op_categories):
    bg,fg=op_colors[cat]
    with columns[i%3]:placar(cat,op_counts[cat],bg,fg)
if op_counts["Não informado"]:
    st.info(f"Situação operacional não identificada para {op_counts['Não informado']} placas. Não serão classificadas automaticamente como em operação. Para calcular o saldo operacional, o CSV precisa ter uma coluna específica de situação operacional.")
operating=snap[snap.situacao=="Em operação"]
st.markdown("**Saúde dos veículos em operação**")
sc=st.columns(4)
health_colors={"Saudável":("#628D4F","#FFFFFF"),"Avalie":("#C83E45","#FFFFFF"),"Atenção":("#E9AD43","#382800"),"Sem informação":("#E5E7EB","#252B36")}
for i,cat in enumerate(["Saudável","Avalie","Atenção","Sem informação"]):
    bg,fg=health_colors[cat]
    with sc[i]:placar(cat,int((operating.estado==cat).sum()),bg,fg)
with st.expander("Consultar placas da frota por situação"):
    choice=st.selectbox("Situação operacional",["Todas"]+op_categories,key="fleet_op")
    health_choice=st.selectbox("Situação de saúde",["Todas","Saudável","Avalie","Atenção","Sem informação"],key="fleet_health")
    fleet=snap.copy()
    if choice!="Todas":fleet=fleet[fleet.situacao==choice]
    if health_choice!="Todas":fleet=fleet[fleet.estado==health_choice]
    st.dataframe(fleet[["placa","situacao","estado"]].rename(columns={"placa":"Placa","situacao":"Situação operacional","estado":"Saúde"}),hide_index=True,use_container_width=True)
    st.download_button("Baixar placas da frota",csv_bytes(fleet),"frota_filtrada.csv","text/csv")
st.divider()
st.subheader("2 · Resultado dos serviços")
# A sequência de atendimentos deve ser calculada ANTES de filtrar a modalidade
# de execução: caso contrário, retornos cruzados desaparecem do histórico.
history=services.copy()
repeat_rows=[]
if not history.empty:
    history=history.sort_values(["placa","dt","os"],kind="stable")
    history["os_chave"]=history["os"].fillna("").astype(str).str.strip()
    # Uma mesma OS importada em CSVs sobrepostos não é um novo atendimento.
    history=history.drop_duplicates(subset=["placa","os_chave"],keep="last")
    for placa,grupo in history.groupby("placa",sort=False):
        grupo=grupo.sort_values(["dt","os"],kind="stable")
        anterior=None
        for _,atual in grupo.iterrows():
            if anterior is not None:
                origem=anterior["Tipo de execução"]
                destino=atual["Tipo de execução"]
                if origem=="Própria" and destino=="Terceira":categoria="Própria → Terceira"
                elif origem=="Terceira" and destino=="Própria":categoria="Terceira → Própria"
                elif origem==destino and origem in ("Própria","Terceira"):categoria="Mesma modalidade"
                else:categoria="Não identificado"
                repeat_rows.append({
                    "Placa":placa,"Primeira OS":str(anterior["os"]),
                    "Data inicial":anterior["dt"],"Oficina inicial":anterior["oficina"],
                    "OS seguinte":str(atual["os"]),"Data seguinte":atual["dt"],
                    "Oficina seguinte":atual["oficina"],
                    "Intervalo (dias)":(atual["dt"]-anterior["dt"]).days,
                    "Transição":categoria,
                    "Tipo inicial":anterior["classe"],"Tipo seguinte":atual["classe"]
                })
            anterior=atual
repeat_df=pd.DataFrame(repeat_rows)
if repeat_filter!="Todos os serviços":
    filtered_repeat=repeat_df[repeat_df["Transição"]==repeat_filter].copy() if not repeat_df.empty else pd.DataFrame()
    # Mostrar os atendimentos que participam das sequências selecionadas.
    chosen=set()
    if not filtered_repeat.empty:
        for _,rr in filtered_repeat.iterrows():
            chosen.add((rr["Placa"],rr["Primeira OS"]))
            chosen.add((rr["Placa"],rr["OS seguinte"]))
    services=services[services.apply(lambda rr:(rr["placa"],str(rr["os"])) in chosen,axis=1)].copy()
with st.expander("Analisar possíveis retrabalhos",expanded=False):
    st.caption("Repetição de placa em OS distintas é indício de retorno, não comprovação de retrabalho. Sequências calculadas antes do filtro de execução, usando o período e tipos de serviço selecionados.")
    if repeat_df.empty:
        st.info("Nenhuma placa com duas OS distintas nos filtros de período e serviço.")
    else:
        report=repeat_df if repeat_filter=="Todos os serviços" else repeat_df[repeat_df["Transição"]==repeat_filter]
        if exec_filter!="Geral":
            # Uma transição cruzada deve continuar visível se envolver a modalidade escolhida.
            report=report[report["Transição"].map(lambda x: exec_filter=="Própria" and "Própria" in x or exec_filter=="Terceira" and "Terceira" in x or exec_filter=="Não identificado" and x=="Não identificado")]
        x,y,z=st.columns(3)
        x.metric("OS únicas",len(history))
        y.metric("Placas com retorno",report["Placa"].nunique())
        z.metric("Retornos cruzados",int(report["Transição"].isin(["Própria → Terceira","Terceira → Própria"]).sum()))
        show=report.copy()
        for dc in ("Data inicial","Data seguinte"):
            show[dc]=pd.to_datetime(show[dc]).dt.strftime("%d/%m/%Y")
        st.dataframe(show,hide_index=True,use_container_width=True)
        st.download_button("Baixar possíveis retornos",csv_bytes(show),"possiveis_retornos.csv","text/csv")
if not services.empty:
    counts=services["Tipo de execução"].value_counts()
    st.caption("Classificação pelo nome da oficina executora; a coluna Cliente Oficina Própria não é utilizada.")
    c1,c2,c3,c4=st.columns(4)
    for column, name in zip((c1,c2,c3,c4),("Geral","Própria","Terceira","Não identificado")):
        with column:
            st.metric(name, len(services) if name=="Geral" else int(counts.get(name,0)))
    if int(counts.get("Não identificado",0)):
        with st.expander("Revisar oficinas não identificadas"):
            st.dataframe(services.loc[services["Tipo de execução"]=="Não identificado",["oficina","cliente"]].drop_duplicates(),hide_index=True,use_container_width=True)
    if exec_filter!="Geral":
        services=services[services["Tipo de execução"]==exec_filter].copy()
st.caption(f"OS concluídas de {start.strftime('%d/%m/%Y')} a {date.fromisoformat(end).strftime('%d/%m/%Y')} · situação na fotografia de {date.fromisoformat(end).strftime('%d/%m/%Y')}")
if services.empty:
    st.warning("Não há OS concluídas dos tipos selecionados no período. Confira CSVs, datas e filtro de clientes.")
    st.stop()
services=services.sort_values("dt")
plates=services.placa.dropna().unique().tolist()
by_plate=[]
for pl in plates:
    os_rows=services[services.placa==pl]
    last=os_rows.iloc[-1]
    now=current.get(pl,"Sem informação" if coverage.get(end)!="completa" else "Não localizado")
    earlier=photos[photos.data_base<end].data_base.tolist()
    prev="Sem informação"
    prev_date=""
    # Fotografia anterior mais recente que seja anterior à OS mais antiga da placa.
    first_os=os_rows["dt"].min().date()
    for d in reversed(earlier):
        if date.fromisoformat(d)>=first_os: continue
        matching=health[(health.data_base==d)&(health.placa==pl)]
        if not matching.empty:
            prev=status(matching.iloc[-1].status);prev_date=d;break
    movement=evolve(prev,now)
    if movement=="Melhorou":result="Melhorou (confirmado entre fotografias)"
    elif movement=="Piorou":result="Piorou (confirmado entre fotografias)"
    elif movement=="Permaneceu igual":result="Sem mudança confirmada"
    else:result="Sem comparação anterior"
    by_plate.append({"Placa":pl,"OS concluídas":len(os_rows),"Última OS":str(last["os"]),"Último serviço":pd.to_datetime(last["dt"],errors="coerce").strftime("%d/%m/%Y") if pd.notna(pd.to_datetime(last["dt"],errors="coerce")) else "Data não informada","Tipo":last.classe,"Tipo de execução":last["Tipo de execução"],"Oficina":last.oficina,"Cliente":last.cliente,"Saúde atual":now,"Situação operacional":current_op.get(pl,"Não informado"),"Saúde anterior":prev,"Data anterior":prev_date,"Evolução":result})
detail=pd.DataFrame(by_plate)
valid=detail["Saúde atual"].isin(["Saudável","Avalie","Atenção"])
healthy=int((detail["Saúde atual"]=="Saudável").sum())
avalie=int((detail["Saúde atual"]=="Avalie").sum())
atencao=int((detail["Saúde atual"]=="Atenção").sum())
unknown=int((~valid).sum())
a,b=st.columns(2)
with a:placar("OS concluídas no período",len(services),"#173A67")
with b:placar("Placas distintas atendidas",len(detail),"#20252B")
st.markdown("**Como estão hoje as placas atendidas?**")
status_cards=st.columns(4)
for i,(name,num) in enumerate([("Saudável",healthy),("Avalie",avalie),("Atenção",atencao),("Sem informação",unknown)]):
    bg,fg=health_colors[name]
    with status_cards[i]:placar(name,num,bg,fg)
st.caption("Os números acima são placas únicas; uma mesma placa pode ter várias OS. Saúde atual não comprova recuperação causada pelo serviço.")
if (services.dt.dt.date==date.fromisoformat(end)).any():
    st.warning("Há OS no mesmo dia da fotografia. Sem horários confiáveis, o status pode ter sido coletado antes do serviço.")
choice=st.radio("Clique para listar as placas",["Todas","Saudável","Avalie","Atenção","Sem informação"],horizontal=True,key="service_status")
shown=detail.copy()
if choice=="Sem informação":shown=shown[~shown["Saúde atual"].isin(["Saudável","Avalie","Atenção"])]
elif choice!="Todas":shown=shown[shown["Saúde atual"]==choice]
st.dataframe(shown[["Placa","Último serviço","Tipo","OS concluídas","Última OS","Tipo de execução","Saúde atual","Situação operacional"]],hide_index=True,use_container_width=True)
st.download_button("⬇️ Baixar placas atendidas",csv_bytes(shown),"placas_atendidas.csv","text/csv")
restrictions=detail[detail["Situação operacional"].isin(["Fora de operação","Em manutenção","Cancelado","Sinistrado"])]
if not restrictions.empty:
    st.warning(f"{len(restrictions)} placas atendidas constam com restrição operacional na fotografia atual. Verifique as datas: a restrição atual não comprova que existia quando a OS foi executada.")
    with st.expander("Ver OS de placas com restrição operacional"):
        st.dataframe(restrictions,hide_index=True,use_container_width=True)
with st.expander("Ver todas as OS e detalhes técnicos"):
    raw=services.copy()
    raw["Data OS"]=raw.dt.dt.strftime("%d/%m/%Y %H:%M")
    raw["Saúde atual"]=raw.placa.map(lambda x:current.get(x,"Sem informação" if coverage.get(end)!="completa" else "Não localizado"))
    st.dataframe(raw[["Data OS","os","placa","cliente","classe","tipo","oficina","Tipo de execução","Saúde atual"]],hide_index=True,use_container_width=True)
    st.download_button("⬇️ Baixar todas as OS",csv_bytes(raw.drop(columns=["dt"])),"servicos_periodo.csv","text/csv")
with st.expander("Ver fotografias e histórico da frota"):
    sums=health.groupby("data_base").agg(placas=("placa","nunique")).reset_index()
    st.dataframe(photos.merge(sums,on="data_base",how="left"),hide_index=True,use_container_width=True)
    st.caption("Arquivos filtrados não representam a frota total. Uma placa ausente não é automaticamente saudável.")
    if len(dates)>1:
        previous=[x for x in dates if x<end]
        if previous:
            old=st.selectbox("Comparar fotografia atual com",previous,index=len(previous)-1)
            oldmap=dict(zip(health[health.data_base==old].placa,health[health.data_base==old].status.map(status)))
            evolution=detail[["Placa","Saúde atual"]].copy()
            evolution["Saúde em "+old]=evolution.Placa.map(lambda p:oldmap.get(p,"Sem informação"))
            evolution["Evolução"]=evolution.apply(lambda r:evolve(r["Saúde em "+old],r["Saúde atual"]),axis=1)
            st.dataframe(evolution,hide_index=True,use_container_width=True)
            st.download_button("⬇️ Baixar comparação",csv_bytes(evolution),"comparacao_fotografias.csv","text/csv")
st.caption("O painel distingue saúde e situação operacional. Restrições atuais são sinalizadas, mas não expurgam automaticamente OS históricas sem comprovação da restrição na data do serviço.")
