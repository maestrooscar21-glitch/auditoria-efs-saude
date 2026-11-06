import streamlit as st
import pandas as pd
import io, re, unicodedata
st.set_page_config(page_title='Auditoria Pós-Serviço',page_icon='🚛',layout='wide')
st.title('🚛 Auditoria Pós-Serviço | EFS × Saúde do Veículo')
st.caption('Importe atividades EFS e um retrato de saúde atual. Opcionalmente, importe um retrato anterior para medir evolução.')
def norm(s):
    return ''.join(c for c in unicodedata.normalize('NFKD',str(s)) if not unicodedata.combining(c)).upper().strip()
def read(f):
    data=f.getvalue()
    for enc in ['utf-8-sig','cp1252','latin1']:
        for sep in [',',';','\t']:
            try:
                df=pd.read_csv(io.BytesIO(data),encoding=enc,sep=sep,dtype=str,low_memory=False)
                if len(df.columns)>2:
                    df.columns=[str(c).strip() for c in df.columns]
                    return df
            except Exception:pass
    raise ValueError('Não foi possível ler '+f.name)
def col(df,name):
    return next((c for c in df.columns if norm(c)==norm(name)),None)
def cleanplate(x):return re.sub('[^A-Z0-9]','',norm(x)) if pd.notna(x) else ''
def health(f):
    d=read(f)
    p=col(d,'PLACA'); s=col(d,'STATUS')
    if not p or not s:raise ValueError('Saúde deve conter PLACA e STATUS')
    d['_placa']=d[p].map(cleanplate)
    d=d[d['_placa']!=''].drop_duplicates('_placa',keep='last')
    return d.set_index('_placa')
def family(v):
    v=norm(v)
    return any(k in v for k in ['ROTA TRANSP','CIDADE SOL','GRUPO BRASILEIRO','BRASILEIRO ROTA','VIACAO GQE','AVIACAO GQE','GQE'])
def rank(v):
    v=norm(v)
    if 'SAUD' in v:return 2
    if 'AVAL' in v:return 1
    if 'ATEN' in v:return 0
    return None
with st.sidebar:
    st.header('Arquivos')
    fs=st.file_uploader('CSVs EFS (vários dias)',type=['csv'],accept_multiple_files=True)
    atual=st.file_uploader('Saúde atual',type=['csv'],key='atual')
    anterior=st.file_uploader('Saúde anterior (opcional)',type=['csv'],key='anterior')
    st.header('Filtros')
    somente=st.checkbox('Somente Grupo Rota',value=True)
    outros=st.text_input('Outros nomes de cliente (separados por ;)',value='')
    st.caption('Grupo Rota inclui Rota Transportes, Cidade Sol, Grupo Brasileiro, Brasileiro Rota e GQE.')
if not fs or not atual:
    st.info('Envie pelo menos um CSV EFS e a base de saúde atual para começar.')
    st.stop()
try:
    atividades=pd.concat([read(f) for f in fs],ignore_index=True)
    placa=col(atividades,'Placa'); cliente=col(atividades,'Cliente'); tipo=col(atividades,'Tipo de Atividade'); status=col(atividades,'Status da Atividade')
    if not all([placa,cliente,tipo,status]):raise ValueError('CSV EFS precisa de Placa, Cliente, Tipo de Atividade e Status da Atividade')
    atividades['_placa']=atividades[placa].map(cleanplate)
    atividades=atividades[atividades['_placa']!=''].copy()
    atividades=atividades[atividades[status].map(norm).str.contains('CONCLUID',na=False)].copy()
    termos=[norm(v) for v in outros.split(';') if v.strip()]
    if somente:atividades=atividades[atividades[cliente].map(lambda x: family(x) or any(t in norm(x) for t in termos))]
    atividades['_classe']=atividades[tipo].map(lambda x:'Manutenção' if 'MANUTEN' in norm(x) else ('Instalação' if 'INSTAL' in norm(x) else 'Outros'))
    # OS é a chave preferencial para eliminar arquivos diários repetidos
    oscol=col(atividades,'OS'); idcol=col(atividades,'ID da Atividade')
    if oscol:
        atividades['_chave']=atividades[oscol].fillna('').str.strip()
        atividades.loc[atividades['_chave']=='','_chave']='SEM_OS_'+atividades.index.astype(str)
    elif idcol:atividades['_chave']=atividades[idcol].fillna('').astype(str)
    else:atividades['_chave']=atividades.index.astype(str)
    atividades=atividades.drop_duplicates('_chave',keep='last')
    sa=health(atual)
    prev=health(anterior) if anterior else None
    saida=atividades.copy()
    saida['Saúde atual']=saida['_placa'].map(sa[col(sa,'STATUS')])
    if prev is not None:
        saida['Saúde anterior']=saida['_placa'].map(prev[col(prev,'STATUS')])
        def evol(row):
            a,b=rank(row['Saúde anterior']),rank(row['Saúde atual'])
            if a is None or b is None:return 'Sem comparação'
            return 'Melhorou' if b>a else ('Piorou' if b<a else 'Permaneceu igual')
        saida['Evolução']=saida.apply(evol,axis=1)
    else:saida['Evolução']='Sem base anterior'
    saida['Situação']=saida['Saúde atual'].fillna('Placa não localizada')
    a,b,c,d=st.columns(4)
    a.metric('OS concluídas',len(saida));b.metric('Placas únicas',saida['_placa'].nunique())
    c.metric('Placas localizadas',saida.loc[saida['Saúde atual'].notna(),'_placa'].nunique())
    d.metric('Placas não localizadas',saida.loc[saida['Saúde atual'].isna(),'_placa'].nunique())
    st.subheader('Saúde por tipo de serviço (placas únicas)')
    unique=saida.drop_duplicates(['_classe','_placa'])
    resumo=unique.groupby(['_classe','Situação']).size().unstack(fill_value=0)
    resumo['Total']=resumo.sum(axis=1)
    resumo['% saudável']=(unique.assign(ok=unique['Situação'].map(lambda x:'SAUD' in norm(x))).groupby('_classe')['ok'].mean()*100).round(1)
    st.dataframe(resumo,use_container_width=True)
    st.bar_chart(unique.groupby(['_classe','Situação']).size().unstack(fill_value=0))
    if prev is not None:
        st.subheader('Evolução entre bases de saúde')
        st.dataframe(unique.groupby(['_classe','Evolução']).size().unstack(fill_value=0),use_container_width=True)
        st.caption('Melhora/piora compara somente categorias de status; não comprova solução ou reincidência do defeito da OS.')
    st.subheader('Detalhamento das atividades')
    filtro=st.multiselect('Tipo de serviço',sorted(saida['_classe'].unique()),default=sorted(saida['_classe'].unique()))
    detalhe=saida[saida['_classe'].isin(filtro)].copy()
    principais=[x for x in [placa,cliente,oscol,col(saida,'Data'),tipo,status,col(saida,'Oficina'),'Saúde anterior','Saúde atual','Evolução',col(saida,'Diagnóstico Previsto.1'),col(saida,'Observações do Técnico')] if x and x in detalhe.columns]
    st.dataframe(detalhe[principais],use_container_width=True,hide_index=True)
    st.download_button('📥 Baixar cruzamento em CSV',detalhe[principais].to_csv(index=False,sep=';').encode('utf-8-sig'),'cruzamento_efs_saude.csv','text/csv')
    st.caption('A comparação é por placa. Placas com várias OS podem aparecer várias vezes no detalhamento, mas são únicas nos indicadores por categoria.')
except Exception as e:st.error(f'Não foi possível processar: {e}')
