"""Visão gerencial de estoque — somente leitura, integrada ao Supabase existente."""
import pandas as pd
import streamlit as st
from supabase_backend import fetch

st.set_page_config(page_title='Estoque | Visão Gerencial', layout='wide')
st.title('📦 Estoque | Visão Gerencial')
st.caption('Consulta gerencial • saldos documentais não representam inventário físico confirmado')

@st.cache_data(ttl=300, show_spinner=False)
def dados():
    tabelas = {
        'remessas': 'id,numero_pedido,oficina,data_expedicao,data_recebimento,status_conferencia',
        'remessa_itens': 'remessa_id,codigo_item,descricao,quantidade_solicitada,quantidade_recebida',
        'consumos': 'numero_os,oficina,cliente,data_execucao,codigo_item,descricao,quantidade,situacao',
        'inventarios': 'oficina,data_contagem,codigo_item,descricao,disponivel,defeituoso,devolucao,reservado',
        'ajustes_estoque': 'oficina,codigo_item,quantidade,data_ajuste,motivo',
    }
    return {t: pd.DataFrame(fetch(t, c)) for t,c in tabelas.items()}

try:
    base = dados()
except Exception as exc:
    st.error(f'Não foi possível consultar as tabelas de estoque: {exc}')
    st.stop()

rem, itens, con, inv, aj = (base[k].copy() for k in ('remessas','remessa_itens','consumos','inventarios','ajustes_estoque'))
if not itens.empty and not rem.empty:
    entrada = itens.merge(rem[['id','oficina','numero_pedido','data_recebimento','status_conferencia']], left_on='remessa_id',right_on='id',how='left')
else:
    entrada = pd.DataFrame(columns=['oficina','codigo_item','descricao','quantidade_solicitada','quantidade_recebida','numero_pedido'])

for df, cols in [(entrada,['quantidade_solicitada','quantidade_recebida']), (con,['quantidade']), (aj,['quantidade']), (inv,['disponivel','defeituoso','devolucao','reservado'])]:
    for col in cols:
        if col not in df: df[col] = 0
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

# Quantidades solicitadas são exibidas separadamente; nunca entram como recebimento confirmado.
if not entrada.empty:
    e = entrada.groupby(['oficina','codigo_item'],dropna=False).agg(solicitado=('quantidade_solicitada','sum'),recebido=('quantidade_recebida','sum')).reset_index()
else:
    e = pd.DataFrame(columns=['oficina','codigo_item','solicitado','recebido'])
c = con.groupby(['oficina','codigo_item'],dropna=False)['quantidade'].sum().rename('consumido').reset_index() if not con.empty else pd.DataFrame(columns=['oficina','codigo_item','consumido'])
a = aj.groupby(['oficina','codigo_item'],dropna=False)['quantidade'].sum().rename('ajuste').reset_index() if not aj.empty else pd.DataFrame(columns=['oficina','codigo_item','ajuste'])
res = e.merge(c,on=['oficina','codigo_item'],how='outer').merge(a,on=['oficina','codigo_item'],how='outer')
for col in ['solicitado','recebido','consumido','ajuste']:
    if col not in res: res[col] = 0
    res[col] = pd.to_numeric(res[col],errors='coerce').fillna(0)
res['saldo_documental'] = res['recebido'] - res['consumido'] + res['ajuste']
res['alerta'] = res.apply(lambda x: '⚠️ Saldo negativo' if x.saldo_documental < 0 else ('⚠️ Abaixo de 50%' if x.recebido > 0 and x.saldo_documental < x.recebido*0.5 else '—'),axis=1)

oficinas = sorted(set(res['oficina'].dropna().astype(str)) | set(inv['oficina'].dropna().astype(str)) | set(rem['oficina'].dropna().astype(str)))
escolha = st.selectbox('Oficina', ['Todas'] + oficinas)
if escolha != 'Todas':
    res = res[res['oficina'].astype(str)==escolha]
    inv = inv[inv['oficina'].astype(str)==escolha]
    con = con[con['oficina'].astype(str)==escolha]
    entrada = entrada[entrada['oficina'].astype(str)==escolha]

st.info('Regra: saldo documental = quantidade recebida confirmada − consumo registrado + ajustes. Pedidos solicitados não são tratados como recebimentos.')
cols = st.columns(5)
valores = [('Solicitado',res['solicitado'].sum()),('Recebido registrado',res['recebido'].sum()),('Consumo registrado',res['consumido'].sum()),('Saldo documental',res['saldo_documental'].sum()),('Itens com alerta',int(res['alerta'].ne('—').sum()))]
for col,(nome,valor) in zip(cols,valores):
    col.metric(nome, f'{valor:,.0f}'.replace(',','.'))

if all(x.empty for x in (rem, itens, con, inv, aj)):
    st.warning('As tabelas de estoque ainda não possuem registros. O módulo está conectado e pronto para apresentar dados após a carga inicial.')
else:
    st.subheader('Posição por oficina e código')
    st.dataframe(res.rename(columns={'oficina':'Oficina','codigo_item':'Código','solicitado':'Solicitado','recebido':'Recebido','consumido':'Consumido','ajuste':'Ajuste','saldo_documental':'Saldo documental','alerta':'Alerta'}),use_container_width=True,hide_index=True)
    st.download_button('Exportar posição CSV',res.to_csv(index=False,sep=';').encode('utf-8-sig'),file_name='estoque_posicao_gerencial.csv',mime='text/csv')
    if not inv.empty:
        st.subheader('Inventário físico informado (separado do saldo documental)')
        st.dataframe(inv,use_container_width=True,hide_index=True)
    with st.expander('Remessas e consumos registrados'):
        st.write('Remessas')
        st.dataframe(entrada,use_container_width=True,hide_index=True)
        st.write('Consumos')
        st.dataframe(con,use_container_width=True,hide_index=True)

st.caption('Escopo: consulta somente leitura. Não altera remessas, consumos, inventários, OS ou fotografias da frota.')
