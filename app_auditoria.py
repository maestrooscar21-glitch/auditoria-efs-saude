import streamlit as st
import pandas as pd
import sqlite3, io, re, unicodedata, hashlib, os
from datetime import date

st.set_page_config(page_title='Auditoria EFS × Saúde',page_icon='🚛',layout='wide')
st.title('🚛 Auditoria Pós-Serviço | EFS × Saúde')
st.caption('Cruzamento de serviços concluídos com a saúde do veículo e comparação de retratos históricos.')
DB='auditoria_local.sqlite3'

def norm(v):
    return ''.join(c for c in unicodedata.normalize('NFKD',str(v)) if not unicodedata.combining(c)).upper().strip()
def plate(v):
    return re.sub(r'[^A-Z0-9]','',norm(v)) if pd.notna(v) else ''
def read_file(f):
    raw=f.getvalue()
    if f.name.lower().endswith(('.xlsx','.xlsm')):
        df=pd.read_excel(io.BytesIO(raw),dtype=str,engine='openpyxl')
        df.columns=[str(c).strip() for c in df.columns]
        return df
    for enc in ('utf-8-sig','cp1252','latin1'):
        for sep in (',',';','\t'):
            try:
                df=pd.read_csv(io.BytesIO(raw),encoding=enc,sep=sep,dtype=str,low_memory=False)
                if len(df.columns)>2:
                    df.columns=[str(c).strip() for c in df.columns]
                    return df
            except Exception: pass
    raise ValueError('Não foi possível ler '+f.name)
def column(df,*names):
    for name in names:
        for c in df.columns:
            if norm(c)==norm(name): return c
    return None
def family(v):
    v=norm(v)
    return any(x in v for x in ('ROTA TRANSP','CIDADE SOL','GRUPO BRASILEIRO','BRASILEIRO ROTA','VIACAO GQE','AVIACAO GQE','GQE'))
def category(v):
    v=norm(v)
    if 'SAUD' in v:return 'Saudável'
    if 'AVAL' in v:return 'Avalie'
    if 'ATEN' in v:return 'Atenção'
    return 'Sem informação'
def order(v): return {'Atenção':0,'Avalie':1,'Saudável':2}.get(v)
def initialize():
    with sqlite3.connect(DB) as cn:
        cn.execute('CREATE TABLE IF NOT EXISTS servicos (chave TEXT PRIMARY KEY, placa TEXT, cliente TEXT, os TEXT, data TEXT, tipo TEXT, classe TEXT, status TEXT, oficina TEXT, origem TEXT)')
        cn.execute('CREATE TABLE IF NOT EXISTS saude (data_base TEXT, placa TEXT, status TEXT, origem TEXT, PRIMARY KEY(data_base,placa))')
def save_services(files):
    total=0
    with sqlite3.connect(DB) as cn:
        for f in files:
            df=read_file(f)
            p=column(df,'Placa'); cli=column(df,'Cliente'); typ=column(df,'Tipo de Atividade'); sta=column(df,'Status da Atividade')
            if not all((p,cli,typ,sta)):raise ValueError(f'{f.name}: colunas obrigatórias ausentes')
            oscol=column(df,'OS'); idcol=column(df,'ID da Atividade'); dt=column(df,'Data'); ofic=column(df,'Oficina')
            for idx,r in df.iterrows():
                pl=plate(r[p]); status=str(r[sta])
                if not pl or 'CONCLUID' not in norm(status) or 'NAO CONCLUID' in norm(status):continue
                tipo=str(r[typ]); classe='Manutenção' if 'MANUTEN' in norm(tipo) else ('Instalação' if 'INSTAL' in norm(tipo) else 'Outros')
                osid=str(r[oscol]).strip() if oscol and pd.notna(r[oscol]) else ''
                activity=str(r[idcol]).strip() if idcol and pd.notna(r[idcol]) else ''
                data=str(r[dt]).strip() if dt and pd.notna(r[dt]) else ''
                key='OS:'+osid if osid and osid.lower()!='nan' else ('ID:'+activity if activity else 'HASH:'+hashlib.sha256((pl+str(r[cli])+tipo+data).encode()).hexdigest())
                cn.execute('INSERT OR REPLACE INTO servicos VALUES (?,?,?,?,?,?,?,?,?,?)',(key,pl,str(r[cli]),osid,data,tipo,classe,status,str(r[ofic]) if ofic else '',f.name))
                total+=1
    return total
def save_health(f,dt):
    df=read_file(f); p=column(df,'PLACA'); s=column(df,'STATUS')
    if not p or not s:raise ValueError('Saúde precisa conter PLACA e STATUS')
    count=0
    with sqlite3.connect(DB) as cn:
        for _,r in df.iterrows():
            pl=plate(r[p])
            if not pl:continue
            cn.execute('INSERT OR REPLACE INTO saude VALUES (?,?,?,?)',(str(dt),pl,str(r[s]),f.name));count+=1
    return count
initialize()
with st.sidebar:
    st.header('Importar arquivos')
    fs=st.file_uploader('Atividades EFS (vários CSVs)',type='csv',accept_multiple_files=True)
    hf=st.file_uploader('Saúde do veículo (CSV ou Excel)',type=['csv','xlsx'])
    hd=st.date_input('Data da extração da saúde',value=date.today())
    st.caption('Importe uma base de cada vez e clique em Salvar. Para o histórico use 27/10, 02/11 e 06/11 conforme o arquivo.')
    if st.button('💾 Salvar arquivos no histórico',type='primary',use_container_width=True):
        try:
            n=save_services(fs) if fs else 0
            h=save_health(hf,hd) if hf else 0
            st.success(f'Processados: {n} registros de serviço e {h} linhas de saúde. OS repetidas são consolidadas.')
            st.rerun()
        except Exception as e:st.error(str(e))
    st.divider()
    st.header('Backup do banco')
    st.warning('No Streamlit Community Cloud, o banco local NÃO é permanente. Baixe o backup após cada importação e guarde-o no computador.')
    with open(DB,'rb') as f:
        st.download_button('⬇️ Baixar backup SQLite',f.read(),'auditoria_local.sqlite3','application/octet-stream',use_container_width=True)
    backup=st.file_uploader('Restaurar backup SQLite',type=['sqlite3','db'])
    if st.button('Restaurar backup',disabled=backup is None,use_container_width=True):
        try:
            raw=backup.getvalue()
            if not raw.startswith(b'SQLite format 3\x00'):raise ValueError('Arquivo não é um banco SQLite válido')
            with open(DB+'.tmp','wb') as f:f.write(raw)
            with sqlite3.connect(DB+'.tmp') as cn:
                ok=cn.execute('PRAGMA integrity_check').fetchone()[0]
                tables={x[0] for x in cn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if ok!='ok' or not {'servicos','saude'}.issubset(tables):raise ValueError('Backup inválido ou incompatível')
            os.replace(DB+'.tmp',DB);st.success('Backup restaurado');st.rerun()
        except Exception as e:st.error(str(e))
with sqlite3.connect(DB) as cn:
    services=pd.read_sql_query('SELECT * FROM servicos',cn)
    health=pd.read_sql_query('SELECT * FROM saude',cn)
if services.empty or health.empty:
    st.info('Importe ao menos um CSV EFS e uma base de saúde, depois clique em Salvar arquivos no histórico.')
    st.stop()
with st.sidebar:
    st.header('Filtros')
    rota=st.checkbox('Somente Grupo Rota',value=True)
    custom=st.text_input('Outros nomes do grupo (separados por ;)')
    classes=st.multiselect('Tipo de serviço',sorted(services.classe.unique()),default=[x for x in ('Manutenção','Instalação') if x in services.classe.unique()])
    datas=sorted(health.data_base.unique())
    current=st.selectbox('Saúde atual (data)',datas,index=len(datas)-1)
    previous=st.selectbox('Saúde anterior (opcional)',['Sem comparação']+[x for x in datas if x<current])
terms=[norm(x) for x in custom.split(';') if x.strip()]
if rota:services=services[services.cliente.map(lambda x:family(x) or any(t in norm(x) for t in terms))]
services=services[services.classe.isin(classes)].copy()
services['_data_servico']=pd.to_datetime(services['data'],dayfirst=True,errors='coerce',format='mixed')
data_corte=pd.Timestamp(current)+pd.Timedelta(days=1)
fora=services['_data_servico'].notna() & (services['_data_servico']>=data_corte)
if fora.any():
    st.info(f'{int(fora.sum())} OS com data posterior à base de saúde selecionada foram excluídas deste retrato.')
services=services[~fora].copy()
if services.empty:st.warning('Nenhum serviço encontrado com os filtros atuais.');st.stop()
cur=health[health.data_base==current].drop_duplicates('placa',keep='last').set_index('placa')['status']
services['Saúde atual']=services.placa.map(cur).map(category)
if previous!='Sem comparação':
    old=health[health.data_base==previous].drop_duplicates('placa',keep='last').set_index('placa')['status']
    services['Saúde anterior']=services.placa.map(old).map(category)
    def evolution(r):
        a,b=order(r['Saúde anterior']),order(r['Saúde atual'])
        if a is None or b is None:return 'Sem comparação'
        return 'Melhorou' if b>a else ('Piorou' if b<a else 'Permaneceu igual')
    services['Evolução']=services.apply(evolution,axis=1)
else:
    services['Saúde anterior']='Sem base anterior';services['Evolução']='Sem comparação'
# Uma placa é contada uma vez no total geral, mesmo com múltiplas OS.
unique=services.sort_values(['_data_servico','chave'],na_position='first').drop_duplicates('placa',keep='last')
counts=unique['Saúde atual'].value_counts()
st.subheader(f'📊 Saúde dos veículos — base de {current}')
cols=st.columns(5)
cols[0].metric('OS concluídas',len(services))
cols[1].metric('Veículos únicos',len(unique))
cols[2].metric('🟢 Saudável',int(counts.get('Saudável',0)))
cols[3].metric('🟡 Avalie',int(counts.get('Avalie',0)))
cols[4].metric('🔴 Atenção',int(counts.get('Atenção',0)))
st.progress(float(counts.get('Saudável',0))/max(len(unique),1),text=f"{100*counts.get('Saudável',0)/len(unique):.1f}% saudáveis entre as placas únicas")
st.caption('Status é o retrato da data escolhida, não uma prova de que o serviço causou ou resolveu o alerta. Uma placa com várias OS conta uma vez no resumo geral.')
st.subheader('🎯 Resumo rápido')
total=len(unique)
a,b,c=st.columns(3)
a.error(f"🔴 ATENÇÃO: {int(counts.get('Atenção',0))} veículos")
b.warning(f"🟡 AVALIE: {int(counts.get('Avalie',0))} veículos")
c.success(f"🟢 SAUDÁVEL: {int(counts.get('Saudável',0))} veículos")
st.subheader('Saúde por tipo de serviço')
byclass=services.drop_duplicates(['classe','placa']).groupby(['classe','Saúde atual']).size().unstack(fill_value=0)
st.dataframe(byclass,use_container_width=True)
st.bar_chart(byclass)
alerts=services[services['Saúde atual'].isin(['Avalie','Atenção'])].copy()
st.subheader(f'🚨 Placas com alerta na base de {current} — {alerts.placa.nunique()} placas')
show=['placa','cliente','os','data','classe','tipo','Saúde anterior','Saúde atual','Evolução','oficina']
alerts=alerts.sort_values('Saúde atual')
st.dataframe(alerts[show],hide_index=True,use_container_width=True)
if previous!='Sem comparação':
    st.subheader(f'📈 Evolução entre {previous} e {current}')
    st.dataframe(unique.groupby('Evolução').size().rename('Veículos'),use_container_width=True)
    st.caption('Comparação entre retratos por placa. A melhoria ou piora de status não comprova causalidade.')
    anteriores=pd.Timestamp(previous)
    posteriores=pd.Timestamp(current)+pd.Timedelta(days=1)
    validos=services['_data_servico'].notna() & (services['_data_servico']>=anteriores) & (services['_data_servico']<posteriores)
    periodo=services[validos].copy()
    st.subheader('🔎 Serviços entre as duas extrações')
    st.caption(f'Somente OS executadas de {previous} até {current}. Estas têm uma base anterior e outra posterior ao serviço.')
    st.dataframe(periodo[show],hide_index=True,use_container_width=True)
    st.download_button('⬇️ Baixar serviços entre as bases',periodo[show].to_csv(index=False,sep=';').encode('utf-8-sig'),'servicos_entre_bases.csv','text/csv')
st.subheader('🗂️ Bases de saúde registradas')
st.dataframe(health.groupby('data_base').agg(placas=('placa','nunique')).reset_index(),hide_index=True,use_container_width=True)
st.subheader('Todas as OS concluídas')
st.dataframe(services[show],hide_index=True,use_container_width=True)
st.download_button('📥 Exportar cruzamento CSV',services[show].to_csv(index=False,sep=';').encode('utf-8-sig'),'cruzamento_efs_saude.csv','text/csv')
st.caption('A data da extração é informada por você. A base de saúde mostra o status naquela data, não necessariamente imediatamente após o serviço. Registros da mesma placa/data são substituídos em novas importações.')
