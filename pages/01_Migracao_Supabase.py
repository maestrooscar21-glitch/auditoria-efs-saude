"""Página temporária de migração, remover do GitHub após validação."""
import hashlib
import hmac
import os
import sqlite3
import tempfile
from pathlib import Path

import requests
import streamlit as st

st.set_page_config(page_title="Migração segura | Supabase", page_icon="🔐", layout="wide")
st.title("🔐 Migração do SQLite para o Supabase")
st.warning("Ferramenta temporária de administração. Remova esta página após a migração.")

try:
    url = str(st.secrets["SUPABASE_URL"]).rstrip("/")
    key = str(st.secrets["SUPABASE_KEY"])
    password = str(st.secrets["MIGRATION_PASSWORD"])
except (KeyError, FileNotFoundError):
    st.error("Configure SUPABASE_URL, SUPABASE_KEY e MIGRATION_PASSWORD nos Secrets do Streamlit.")
    st.stop()

if not url.startswith("https://") or not key.startswith(("sb_secret_", "eyJ")) or len(password) < 12:
    st.error("Configuração inválida. A senha de migração deve ter pelo menos 12 caracteres.")
    st.stop()

entered = st.text_input("Senha de administração da migração", type="password")
if not entered or not hmac.compare_digest(entered, password):
    st.info("Informe a senha administrativa para liberar a migração.")
    st.stop()

st.success("Acesso à ferramenta autorizado.")
file = st.file_uploader("Selecione o backup auditoria_local.sqlite3", type=["sqlite3", "db"])
if not file:
    st.stop()
raw = file.getvalue()
if not raw.startswith(b"SQLite format 3\x00"):
    st.error("O arquivo não é um banco SQLite válido.")
    st.stop()

mapping = {
    "servicos": ("ordens_servico", {"chave":"chave", "os":"numero_os", "placa":"placa", "cliente":"cliente", "data":"data_execucao", "tipo":"tipo", "classe":"classe", "status":"status", "oficina":"oficina", "origem":"origem"}, "chave"),
    "saude": ("saude_frota", {"data_base":"data_base", "placa":"placa", "status":"status_saude", "origem":"origem"}, "data_base,placa"),
    "operacao": ("situacao_frota", {"data_base":"data_base", "placa":"placa", "situacao":"situacao"}, "data_base,placa"),
    "fotografias": ("fotografias_frota", {"data_base":"data_base", "cobertura":"cobertura", "arquivo":"arquivo", "importado_em":"importado_em"}, "data_base"),
}

with tempfile.TemporaryDirectory() as tempdir:
    path = Path(tempdir) / "backup.sqlite3"
    path.write_bytes(raw)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        st.error("O backup não passou na verificação de integridade.")
        st.stop()
    counts = {table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in mapping}
    st.write("Arquivo SHA-256:", hashlib.sha256(raw).hexdigest()[:20] + "…")
    st.dataframe([{"Tabela": t, "Registros": n} for t,n in counts.items()], hide_index=True)
    agree = st.checkbox("Confirmo que desejo copiar esses dados para o NOVO Supabase, sem excluir o SQLite")
    if st.button("Migrar e validar", disabled=not agree, type="primary"):
        headers = {"apikey":key, "Authorization":f"Bearer {key}", "Content-Type":"application/json", "Prefer":"resolution=merge-duplicates,return=minimal"}
        try:
            with st.status("Transferindo e conferindo registros...", expanded=True) as progress:
                for source, (target, columns, conflict) in mapping.items():
                    rows = [{destination: record[origin] for origin,destination in columns.items()} for record in con.execute(f"SELECT * FROM {source}")]
                    for i in range(0, len(rows), 100):
                        r = requests.post(f"{url}/rest/v1/{target}", params={"on_conflict":conflict}, headers=headers, json=rows[i:i+100], timeout=60)
                        r.raise_for_status()
                    check = requests.get(f"{url}/rest/v1/{target}", params={"select":"id" if target=="ordens_servico" else "data_base", "limit":0}, headers={**headers, "Prefer":"count=exact"}, timeout=30)
                    check.raise_for_status()
                    remote_count = int(check.headers["Content-Range"].split("/")[-1])
                    st.write(f"{source} → {target}: {len(rows)} no backup / {remote_count} no Supabase")
                    if remote_count != len(rows):
                        raise RuntimeError(f"Contagem divergente em {target}; interrompido para análise.")
                progress.update(label="Migração concluída e contagens conferidas", state="complete")
            st.success("Os registros foram transferidos. Guarde o backup original e remova esta página temporária após a validação.")
        except Exception as exc:
            st.error(f"Migração interrompida: {type(exc).__name__}. {str(exc)[:350]}")
            st.info("O processo pode ser repetido: as chaves únicas evitam duplicar registros existentes.")
    con.close()
