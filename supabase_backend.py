"""Persistência Supabase para Auditoria EFS x Saúde.
Somente execução no servidor Streamlit. Nunca exponha SUPABASE_KEY no navegador/GitHub.
"""
from datetime import date
import requests
import pandas as pd
import streamlit as st

TIMEOUT = 60
BATCH = 100


def _config():
    try:
        url = str(st.secrets['SUPABASE_URL']).rstrip('/')
        key = str(st.secrets['SUPABASE_KEY'])
    except (KeyError, FileNotFoundError) as exc:
        raise RuntimeError('Configure SUPABASE_URL e SUPABASE_KEY nos Secrets do Streamlit.') from exc
    if not url.startswith('https://') or not key:
        raise RuntimeError('Configuração Supabase inválida.')
    return url, key


def _headers(prefer=None):
    _, key = _config()
    h = {'apikey': key, 'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
    if prefer:
        h['Prefer'] = prefer
    return h


def _request(method, table, *, params=None, payload=None, prefer=None):
    url, _ = _config()
    r = requests.request(method, f'{url}/rest/v1/{table}', headers=_headers(prefer), params=params, json=payload, timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(f'Supabase {table}: HTTP {r.status_code}: {r.text[:450]}')
    return r


def fetch(table, columns='*', order=None):
    """Busca todas as páginas; PostgREST limita o retorno por solicitação."""
    result = []
    offset = 0
    while True:
        params = {'select': columns, 'limit': 1000, 'offset': offset}
        if order:
            params['order'] = order
        page = _request('GET', table, params=params).json()
        result.extend(page)
        if len(page) < 1000:
            break
        offset += len(page)
    return result


def count(table):
    r = _request('GET', table, params={'select': '*', 'limit': 0}, prefer='count=exact')
    return int(r.headers['Content-Range'].split('/')[-1])


def upsert(table, rows, conflict):
    for pos in range(0, len(rows), BATCH):
        _request('POST', table, params={'on_conflict': conflict}, payload=rows[pos:pos+BATCH], prefer='resolution=merge-duplicates,return=minimal')


def delete_day(table, day):
    _request('DELETE', table, params={'data_base': f'eq.{day}'}, prefer='return=minimal')


def load_data():
    """Retorna DataFrames com os MESMOS nomes de coluna usados no painel original."""
    photos = pd.DataFrame(fetch('fotografias_frota', 'data_base,cobertura,arquivo', 'data_base.asc'), columns=['data_base','cobertura','arquivo'])
    health = pd.DataFrame(fetch('saude_frota', 'data_base,placa,status_saude,origem'), columns=['data_base','placa','status_saude','origem'])
    health = health.rename(columns={'status_saude': 'status'})
    services = pd.DataFrame(fetch('ordens_servico', 'chave,placa,cliente,numero_os,data_execucao,tipo,classe,status,oficina,origem'), columns=['chave','placa','cliente','numero_os','data_execucao','tipo','classe','status','oficina','origem'])
    services = services.rename(columns={'numero_os': 'os', 'data_execucao': 'data'})
    operation = pd.DataFrame(fetch('situacao_frota', 'data_base,placa,situacao'), columns=['data_base','placa','situacao'])
    # PostgREST pode retornar datas como strings; manter compatibilidade com filtros do painel.
    for frame in (photos, health, operation):
        if 'data_base' in frame:
            frame['data_base'] = frame['data_base'].astype(str)
    return photos, health, services, operation


def save_services(records):
    """Recebe registros já validados pelo importador EFS original."""
    rows = []
    for rec in records:
        rows.append({
            'chave': rec['chave'], 'placa': rec['placa'], 'cliente': rec['cliente'],
            'numero_os': rec['os'], 'data_execucao': rec['data'],
            'tipo': rec['tipo'], 'classe': rec['classe'], 'status': rec['status'],
            'oficina': rec['oficina'], 'origem': rec['origem']
        })
    upsert('ordens_servico', rows, 'chave')
    return len(rows)


def save_health(day, coverage, filename, records, operations):
    """Substitui uma fotografia. Se falhar no meio, interrompa e reimporte a mesma data.
    A substituição entre várias tabelas não é transacional via REST.
    """
    day = str(day)
    # Primeiro enviar/upsert para não apagar o snapshot antigo em caso de erro de conexão.
    upsert('saude_frota', [{'data_base': day, 'placa': p, 'status_saude': v, 'origem': filename} for p,v in records.items()], 'data_base,placa')
    upsert('situacao_frota', [{'data_base': day, 'placa': p, 'situacao': v} for p,v in operations.items()], 'data_base,placa')
    # Remover placas que não pertencem à nova fotografia, em lotes, com filtro explícito.
    for table, names in [('saude_frota', set(records)), ('situacao_frota', set(operations))]:
        existing = []
        offset = 0
        while True:
            page = _request('GET', table, params={'select': 'placa', 'data_base': f'eq.{day}', 'limit': 1000, 'offset': offset}).json()
            existing.extend(page)
            if len(page) < 1000:
                break
            offset += len(page)
        stale = [row['placa'] for row in existing if row['placa'] not in names]
        for pos in range(0, len(stale), BATCH):
            batch = stale[pos:pos+BATCH]
            quoted = ','.join('"' + p.replace('"','') + '"' for p in batch)
            _request('DELETE', table, params={'data_base': f'eq.{day}', 'placa': f'in.({quoted})'})
    upsert('fotografias_frota', [{'data_base': day, 'cobertura': coverage, 'arquivo': filename}], 'data_base')
    return len(records)
