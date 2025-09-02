# -*- coding: utf-8 -*-
"""
Application Streamlit : Gestion des patients, traitements et séances
Pour les kinésithérapeutes au Maroc (FR)

Fonctionnalités clés :
- Tableau de bord (indicateurs, séances du jour/semaine)
- CRUD Patients (créer, rechercher, modifier, supprimer)
- CRUD Traitements (liés à un patient, progression, facturation simple)
- CRUD Séances (planifier, marquer effectuée/payée, note)
- Exports CSV basiques

Données stockées en SQLite (fichier local "kine.db").

Pour lancer :
    pip install streamlit pandas
    streamlit run app_kine_streamlit.py

Remarque :
- Tout est en une seule page Streamlit pour simplicité.
- Pas d’authentification (à ajouter selon besoin : stauth, OAuth, etc.).
- Les dates sont au format JJ/MM/AAAA côté UI, stockées en ISO (YYYY-MM-DD) côté DB.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, date, time, timedelta
from typing import Optional, List, Tuple, Dict, Any

import pandas as pd
import streamlit as st
from streamlit_calendar import calendar

# ==========================
# Config & Styles
# ==========================
st.set_page_config(
    page_title="Kiné – Gestion Patients",
    page_icon="🧑‍⚕️",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
/********* Tuning visuel simple *********/
:root { --primary: #e11d48; }
.block-container { padding-top: 1.2rem; }
header, .stDeployButton { visibility: hidden; height: 0; }
.metric-card { border-radius: 16px; padding: 16px; box-shadow: 0 1px 8px rgba(0,0,0,0.08); background: #11111111; }
.badge { display:inline-block; padding:2px 8px; border-radius:12px; font-size:12px; }
.badge-ok { background:#10b98122; color:#065f46; }
.badge-warn { background:#f59e0b22; color:#92400e; }
.badge-danger { background:#ef444422; color:#7f1d1d; }
.progress { height:10px; background:#e5e7eb; border-radius:8px; overflow:hidden; }
.progress > div { height:100%; background: var(--primary); }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ==========================
# Helpers
# ==========================
DB_PATH = os.getenv("KINE_DB_PATH", "kine.db")
DATE_FMT_UI = "%d/%m/%Y"  # JJ/MM/AAAA pour l'interface
DATE_FMT_DB = "%Y-%m-%d"  # ISO côté base
TIME_FMT = "%H:%M"  # HH:MM pour l'heure


def today_iso() -> str:
    return date.today().strftime(DATE_FMT_DB)


def to_db_date(ui_value: date | str | None) -> Optional[str]:
    if ui_value is None:
        return None
    if isinstance(ui_value, date):
        return ui_value.strftime(DATE_FMT_DB)
    # chaîne JJ/MM/AAAA
    try:
        return datetime.strptime(ui_value, DATE_FMT_UI).strftime(DATE_FMT_DB)
    except Exception:
        return None


def to_ui_date(db_value: str | None) -> Optional[date]:
    if not db_value:
        return None
    try:
        return datetime.strptime(db_value, DATE_FMT_DB).date()
    except Exception:
        return None


def to_db_time(ui_value: time | str | None) -> Optional[str]:
    if ui_value is None:
        return None
    if isinstance(ui_value, time):
        return ui_value.strftime(TIME_FMT)
    try:
        return datetime.strptime(ui_value, TIME_FMT).strftime(TIME_FMT)
    except Exception:
        return None


def to_ui_time(db_value: str | None) -> Optional[time]:
    if not db_value:
        return None
    try:
        return datetime.strptime(db_value, TIME_FMT).time()
    except Exception:
        return None


# ==========================
# DB Layer
# ==========================
@st.cache_resource(show_spinner=False)
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    with closing(conn.cursor()) as cur:
        cur.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    conn = get_conn()
    with closing(conn.cursor()) as cur:
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT NOT NULL,
                prenom TEXT,
                cin TEXT,
                date_naissance TEXT,
                telephone TEXT,
                email TEXT,
                adresse TEXT,
                notes TEXT,
                created_at TEXT DEFAULT (date('now'))
            );

            CREATE TABLE IF NOT EXISTS traitements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                diagnostic TEXT,
                type_prise_en_charge TEXT,
                date_debut TEXT,
                nb_seances_prevues INTEGER DEFAULT 10,
                tarif_par_seance REAL DEFAULT 0,
                notes TEXT,
                statut TEXT DEFAULT 'En cours', -- En cours | Terminé | Archivé
                created_at TEXT DEFAULT (date('now')),
                FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS seances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                traitement_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                heure TEXT,
                duree_minutes INTEGER DEFAULT 45,
                cout REAL DEFAULT 0,
                effectuee INTEGER DEFAULT 0,
                payee INTEGER DEFAULT 0,
                douleur_avant INTEGER,
                douleur_apres INTEGER,
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(traitement_id) REFERENCES traitements(id) ON DELETE CASCADE
            );
            """
        )
        # Ensure new columns exist when migrating older databases
        try:
            cur.execute("ALTER TABLE patients ADD COLUMN cin TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cur.execute("ALTER TABLE seances ADD COLUMN heure TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cur.execute("ALTER TABLE seances ADD COLUMN cout REAL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        conn.commit()


def run_query(query: str, params: tuple = ()) -> List[sqlite3.Row]:
    conn = get_conn()
    with closing(conn.cursor()) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return rows


def run_exec(query: str, params: tuple = ()) -> int:
    conn = get_conn()
    with closing(conn.cursor()) as cur:
        cur.execute(query, params)
        conn.commit()
        return cur.lastrowid


# Cache des sélections pour performance
@st.cache_data(ttl=10, show_spinner=False)
def list_patients(search: str = "") -> pd.DataFrame:
    """Retrieve patients with consistent DataFrame columns."""
    if search:
        rows = run_query(
            "SELECT * FROM patients WHERE nom LIKE ? OR prenom LIKE ? OR telephone LIKE ? OR cin LIKE ? ORDER BY nom, prenom",
            (f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"),
        )
    else:
        rows = run_query("SELECT * FROM patients ORDER BY nom, prenom")
    columns = [
        "id",
        "nom",
        "prenom",
        "cin",
        "date_naissance",
        "telephone",
        "email",
        "adresse",
        "notes",
        "created_at",
    ]
    return pd.DataFrame([dict(r) for r in rows], columns=columns)


@st.cache_data(ttl=10, show_spinner=False)
def list_traitements(patient_id: Optional[int] = None, statut: Optional[str] = None) -> pd.DataFrame:
    """Retrieve treatments with consistent DataFrame columns."""
    q = "SELECT * FROM traitements"
    clauses: List[str] = []
    params: List[Any] = []
    if patient_id:
        clauses.append("patient_id = ?")
        params.append(patient_id)
    if statut:
        clauses.append("statut = ?")
        params.append(statut)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY date_debut DESC, id DESC"
    rows = run_query(q, tuple(params))
    columns = [
        "id",
        "patient_id",
        "diagnostic",
        "type_prise_en_charge",
        "date_debut",
        "nb_seances_prevues",
        "tarif_par_seance",
        "notes",
        "statut",
        "created_at",
    ]
    return pd.DataFrame([dict(r) for r in rows], columns=columns)


@st.cache_data(ttl=10, show_spinner=False)
def list_seances(
    traitement_id: Optional[int] = None,
    date_min: Optional[str] = None,
    date_max: Optional[str] = None,
) -> pd.DataFrame:
    """Retrieve sessions with consistent DataFrame columns."""
    q = "SELECT * FROM seances"
    clauses: List[str] = []
    params: List[Any] = []
    if traitement_id:
        clauses.append("traitement_id = ?")
        params.append(traitement_id)
    if date_min:
        clauses.append("date >= ?")
        params.append(date_min)
    if date_max:
        clauses.append("date <= ?")
        params.append(date_max)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY date ASC, heure ASC"
    rows = run_query(q, tuple(params))
    columns = [
        "id",
        "traitement_id",
        "date",
        "heure",
        "duree_minutes",
        "cout",
        "effectuee",
        "payee",
        "notes",
        "created_at",
    ]
    return pd.DataFrame([dict(r) for r in rows], columns=columns)


def clear_caches():
    list_patients.clear()
    list_traitements.clear()
    list_seances.clear()


def st_rerun() -> None:
    """Compatibility wrapper to rerun the app across Streamlit versions."""
    try:
        st.experimental_rerun()  # Older versions
    except AttributeError:
        st.rerun()


# ==========================
# UI Components
# ==========================

def metric_card(label: str, value: Any, help_text: Optional[str] = None):
    with st.container(border=True):
        st.markdown(f"**{label}**")
        st.markdown(f"<h2 style='margin:0'>{value}</h2>", unsafe_allow_html=True)
        if help_text:
            st.caption(help_text)


def progression_traitement(t_df: pd.DataFrame, s_df: pd.DataFrame) -> pd.DataFrame:
    if t_df.empty:
        return t_df
    # calc nb seances effectuées et payées
    eff = s_df.groupby("traitement_id")["effectuee"].sum().to_dict() if not s_df.empty else {}
    pay = s_df.groupby("traitement_id")["payee"].sum().to_dict() if not s_df.empty else {}
    t_df = t_df.copy()
    t_df["seances_effectuees"] = t_df["id"].map(lambda i: int(eff.get(i, 0)))
    t_df["seances_payees"] = t_df["id"].map(lambda i: int(pay.get(i, 0)))
    t_df["progress"] = t_df.apply(
        lambda r: (r["seances_effectuees"] / r["nb_seances_prevues"]) if r["nb_seances_prevues"] else 0,
        axis=1,
    )
    t_df["montant_du"] = (t_df["seances_effectuees"] - t_df["seances_payees"]) * t_df["tarif_par_seance"]

    return t_df


# ==========================
# Views
# ==========================

def render_dashboard():
    st.subheader("📊 Tableau de bord")
    if st.button("👤 Gérer les patients"):
        _go_to("patients")

    patients_df = list_patients()
    nb_patients = len(patients_df)
    traitements_en_cours = list_traitements(statut="En cours")

    today = date.today()
    start_week = today - timedelta(days=today.weekday())
    end_week = start_week + timedelta(days=6)

    seances_today = list_seances(date_min=today.strftime(DATE_FMT_DB), date_max=today.strftime(DATE_FMT_DB))
    seances_week = list_seances(date_min=start_week.strftime(DATE_FMT_DB), date_max=end_week.strftime(DATE_FMT_DB))

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_card("Patients", nb_patients)
    with c2:
        metric_card("Traitements en cours", len(traitements_en_cours))
    with c3:
        metric_card("Séances aujourd'hui", len(seances_today))
    with c4:
        metric_card("Séances cette semaine", len(seances_week))

    st.markdown("---")
    st.markdown("### 📅 Séances à venir (7 jours)")
    upcoming = list_seances(date_min=today.strftime(DATE_FMT_DB), date_max=(today + timedelta(days=7)).strftime(DATE_FMT_DB))
    if upcoming.empty:
        st.info("Aucune séance planifiée dans les 7 prochains jours.")
    else:
        t_df = list_traitements()
        p_df = list_patients()
        merged = upcoming.merge(t_df[["id", "patient_id"]], left_on="traitement_id", right_on="id", suffixes=("", "_t")).merge(
            p_df[["id", "nom", "prenom", "telephone"]], left_on="patient_id", right_on="id", suffixes=("", "_p")
        )
        merged = merged[["date", "heure", "nom", "prenom", "telephone"]]
        merged = merged.sort_values(["date", "heure"])
        merged.index = range(1, len(merged) + 1)
        st.dataframe(merged, use_container_width=True)

    st.markdown("---")
    st.markdown("### 🗓️ Calendrier des séances")
    future = list_seances(date_min=today.strftime(DATE_FMT_DB))
    if future.empty:
        st.info("Aucune séance planifiée.")
    else:
        t_df = list_traitements()
        p_df = list_patients()
        merged = future.merge(t_df[["id", "patient_id"]], left_on="traitement_id", right_on="id", suffixes=("", "_t")).merge(
            p_df[["id", "nom", "prenom"]], left_on="patient_id", right_on="id", suffixes=("", "_p")
        )
        events = []
        for _, r in merged.iterrows():
            title = f"{r['nom']} {r['prenom']}".strip()
            start = f"{r['date']} {r['heure'] or '00:00'}"
            events.append({"title": title, "start": start})
        calendar(events, options={"initialView": "dayGridMonth"})


def view_patients():
    st.subheader("👤 Patients")
    search = st.text_input("Recherche (nom, prénom, téléphone)")
    df = list_patients(search)

    with st.expander("➕ Ajouter un patient", expanded=False):
        with st.form("form_add_patient", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                nom = st.text_input("Nom *")
                prenom = st.text_input("Prénom")
                cin = st.text_input("CIN")
                telephone = st.text_input("Téléphone")
                email = st.text_input("Email")
            with c2:
                dtn = st.date_input("Date de naissance", format="DD/MM/YYYY")
                adresse = st.text_area("Adresse")
                notes = st.text_area("Notes")
            submitted = st.form_submit_button("Enregistrer")
            if submitted:
                if not nom.strip():
                    st.error("Le nom est obligatoire.")
                else:
                    run_exec(
                        "INSERT INTO patients (nom, prenom, cin, date_naissance, telephone, email, adresse, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            nom.strip(),
                            prenom.strip(),
                            cin.strip(),
                            to_db_date(dtn),
                            telephone.strip(),
                            email.strip(),
                            adresse.strip(),
                            notes.strip(),
                        ),
                    )
                    clear_caches()
                    st.success("Patient ajouté avec succès.")
                    st_rerun()

    st.markdown("### 📋 Liste des patients")
    if df.empty:
        st.info("Aucun patient trouvé.")
    else:
        st.dataframe(df[["id", "nom", "prenom", "cin", "telephone", "email"]], use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("### ✏️ Modifier / Supprimer")
    if df.empty:
        st.caption("Ajoutez d'abord un patient.")
        return
    id_list = df["id"].tolist()
    sel_id = st.selectbox("Choisir un patient par ID", id_list)
    row = df[df["id"] == sel_id].iloc[0]
    with st.form("form_edit_patient"):
        c1, c2 = st.columns(2)
        with c1:
            nom = st.text_input("Nom *", row["nom"])
            prenom = st.text_input("Prénom", row["prenom"] or "")
            cin = st.text_input("CIN", row["cin"] or "")
            telephone = st.text_input("Téléphone", row["telephone"] or "")
            email = st.text_input("Email", row["email"] or "")
        with c2:
            dtn = st.date_input("Date de naissance", value=to_ui_date(row["date_naissance"]) or date(1990,1,1), format="DD/MM/YYYY")
            adresse = st.text_area("Adresse", row["adresse"] or "")
            notes = st.text_area("Notes", row["notes"] or "")
        c3, c4 = st.columns(2)
        if c3.form_submit_button("💾 Mettre à jour"):
            if not nom.strip():
                st.error("Le nom est obligatoire.")
            else:
                run_exec(
                "UPDATE patients SET nom=?, prenom=?, cin=?, date_naissance=?, telephone=?, email=?, adresse=?, notes=? WHERE id=?",
                    (nom.strip(), prenom.strip(), cin.strip(), to_db_date(dtn), telephone.strip(), email.strip(), adresse.strip(), notes.strip(), sel_id),
                )
                clear_caches()
                st.success("Patient mis à jour.")
                st_rerun()
        if c4.form_submit_button("🗑️ Supprimer", help="Supprime également les traitements et séances associés"):
            run_exec("DELETE FROM patients WHERE id=?", (sel_id,))
            clear_caches()
            st.success("Patient supprimé.")
            st_rerun()

    # ============================
    # Traitements du patient
    # ============================
    st.markdown("---")
    st.markdown("### 📝 Traitements du patient")
    pt_df = list_traitements(patient_id=sel_id)

    with st.expander("➕ Ajouter un traitement", expanded=False):
        with st.form("form_add_traitement_for_patient", clear_on_submit=True):
            diagnostic = st.text_input("Diagnostic / Motif")
            tpec = st.text_input("Type de prise en charge", placeholder="Ex: Lombalgie, Rééducation post-op, etc.")
            date_debut = st.date_input("Date de début", format="DD/MM/YYYY")
            nb_prev = st.number_input("Nombre de séances prévues", min_value=1, max_value=100, value=10)
            tarif = st.number_input("Tarif par séance (MAD)", min_value=0.0, step=10.0, value=0.0)
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Enregistrer")
            if submitted:
                run_exec(
                    "INSERT INTO traitements (patient_id, diagnostic, type_prise_en_charge, date_debut, nb_seances_prevues, tarif_par_seance, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        sel_id,
                        diagnostic.strip(),
                        tpec.strip(),
                        to_db_date(date_debut),
                        int(nb_prev),
                        float(tarif),
                        notes.strip(),
                    ),
                )
                clear_caches()
                st.success("Traitement ajouté.")
                st_rerun()

    if pt_df.empty:
        st.info("Aucun traitement pour ce patient.")
        return

    st.dataframe(
        pt_df[["id", "diagnostic", "type_prise_en_charge", "date_debut", "statut"]],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("### ✏️ Modifier / Clôturer un traitement")
    tid = st.selectbox("Choisir un traitement", pt_df["id"].tolist())
    tr = pt_df[pt_df["id"] == tid].iloc[0]
    with st.form("form_edit_traitement_for_patient"):
        diagnostic = st.text_input("Diagnostic / Motif", tr["diagnostic"] or "")
        tpec = st.text_input("Type de prise en charge", tr["type_prise_en_charge"] or "")
        date_debut = st.date_input("Date de début", to_ui_date(tr["date_debut"]) or date.today(), format="DD/MM/YYYY")
        nb_prev = st.number_input("Nombre de séances prévues", 1, 100, int(tr["nb_seances_prevues"]))
        tarif = st.number_input("Tarif par séance (MAD)", min_value=0.0, step=10.0, value=float(tr["tarif_par_seance"]))
        notes = st.text_area("Notes", tr["notes"] or "")
        statut = st.selectbox("Statut", ["En cours", "Terminé", "Archivé"], index=["En cours", "Terminé", "Archivé"].index(tr["statut"]))
        c1, c2, c3 = st.columns(3)
        if c1.form_submit_button("💾 Mettre à jour"):
            run_exec(
                "UPDATE traitements SET diagnostic=?, type_prise_en_charge=?, date_debut=?, nb_seances_prevues=?, tarif_par_seance=?, notes=?, statut=? WHERE id=?",
                (
                    diagnostic.strip(),
                    tpec.strip(),
                    to_db_date(date_debut),
                    int(nb_prev),
                    float(tarif),
                    notes.strip(),
                    statut,
                    tid,
                ),
            )
            clear_caches()
            st.success("Traitement mis à jour.")
            st_rerun()
        if c2.form_submit_button("🗑️ Supprimer", help="Supprime les séances associées"):
            run_exec("DELETE FROM traitements WHERE id=?", (tid,))
            clear_caches()
            st.success("Traitement supprimé.")
            st_rerun()
        if c3.form_submit_button("✅ Clôturer (Terminé)"):
            run_exec("UPDATE traitements SET statut='Terminé' WHERE id=?", (tid,))
            clear_caches()
            st.success("Traitement clôturé.")
            st_rerun()

    # ============================
    # Séances du traitement
    # ============================
    st.markdown("---")
    st.markdown("### 🗓️ Séances du traitement")
    s_df = list_seances(traitement_id=tid)

    with st.expander("➕ Planifier une séance", expanded=False):
        with st.form("form_add_seance_for_patient", clear_on_submit=True):
            d = st.date_input("Date *", format="DD/MM/YYYY")
            duree = st.number_input("Durée (minutes)", min_value=15, max_value=240, value=45)
            douleur_avant = st.slider("Douleur avant (0-10)", 0, 10, 5)
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Enregistrer")
            if submitted:
                run_exec(
                    "INSERT INTO seances (traitement_id, date, duree_minutes, douleur_avant, notes) VALUES (?, ?, ?, ?, ?)",
                    (
                        tid,
                        to_db_date(d),
                        int(duree),
                        int(douleur_avant),
                        notes.strip(),
                    ),
                )
                clear_caches()
                st.success("Séance planifiée.")
                st_rerun()

    if s_df.empty:
        st.info("Aucune séance pour ce traitement.")
        return

    s_df = s_df.sort_values("date")
    st.dataframe(
        s_df[["id", "date", "duree_minutes", "effectuee", "payee", "douleur_avant", "douleur_apres", "notes"]],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("### ✏️ Modifier une séance")
    sid = st.selectbox("Choisir une séance (ID)", s_df["id"].tolist())
    row = s_df[s_df["id"] == sid].iloc[0]
    with st.form("form_edit_seance_for_patient"):
        d = st.date_input("Date", to_ui_date(row["date"]) or date.today(), format="DD/MM/YYYY")
        duree = st.number_input("Durée (minutes)", 15, 240, int(row["duree_minutes"]))
        effectuee = st.checkbox("Effectuée", value=bool(row["effectuee"]))
        payee = st.checkbox("Payée", value=bool(row["payee"]))
        douleur_avant = st.slider("Douleur avant (0-10)", 0, 10, int(row["douleur_avant"]) if row["douleur_avant"] is not None else 5)
        douleur_apres = st.slider("Douleur après (0-10)", 0, 10, int(row["douleur_apres"]) if row["douleur_apres"] is not None else 3)
        notes = st.text_area("Notes", row["notes"] or "")
        c1, c2 = st.columns(2)
        if c1.form_submit_button("💾 Mettre à jour"):
            run_exec(
                "UPDATE seances SET date=?, duree_minutes=?, effectuee=?, payee=?, douleur_avant=?, douleur_apres=?, notes=? WHERE id=?",
                (
                    to_db_date(d),
                    int(duree),
                    int(effectuee),
                    int(payee),
                    int(douleur_avant),
                    int(douleur_apres),
                    notes.strip(),
                    sid,
                ),
            )
            clear_caches()
            st.success("Séance mise à jour.")
            st_rerun()
        if c2.form_submit_button("🗑️ Supprimer"):
            run_exec("DELETE FROM seances WHERE id=?", (sid,))
            clear_caches()
            st.success("Séance supprimée.")
            st_rerun()


def view_traitements():
    st.subheader("📝 Traitements")
    patients_df = list_patients()
    if patients_df.empty:
        st.info("Ajoutez d'abord un patient pour créer un traitement.")
        return

    with st.expander("➕ Ajouter un traitement", expanded=False):
        with st.form("form_add_traitement", clear_on_submit=True):
            pmap = {f"{r['nom']} {r['prenom']} (#{r['id']})": int(r["id"]) for _, r in patients_df.iterrows()}
            patient_label = st.selectbox("Patient *", list(pmap.keys()))
            diagnostic = st.text_input("Diagnostic / Motif")
            tpec = st.text_input("Type de prise en charge", placeholder="Ex: Lombalgie, Rééducation post-op, etc.")
            date_debut = st.date_input("Date de début", format="DD/MM/YYYY")
            nb_prev = st.number_input("Nombre de séances prévues", min_value=1, max_value=100, value=10)
            tarif = st.number_input("Tarif par séance (MAD)", min_value=0.0, step=10.0, value=0.0)
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Enregistrer")
            if submitted:
                run_exec(
                    "INSERT INTO traitements (patient_id, diagnostic, type_prise_en_charge, date_debut, nb_seances_prevues, tarif_par_seance, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        pmap[patient_label],
                        diagnostic.strip(),
                        tpec.strip(),
                        to_db_date(date_debut),
                        int(nb_prev),
                        float(tarif),
                        notes.strip(),
                    ),
                )
                clear_caches()
                st.success("Traitement ajouté.")
                st_rerun()

    st.markdown("### 📋 Liste des traitements")
    statut = st.selectbox("Filtrer par statut", ["Tous", "En cours", "Terminé", "Archivé"], index=1)
    statut_filter = None if statut == "Tous" else statut
    t_df = list_traitements(statut=statut_filter)
    s_df = list_seances()
    t_df = progression_traitement(t_df, s_df)

    if t_df.empty:
        st.info("Aucun traitement.")
    else:
        # enrichir avec nom/prénom patient
        p_df = list_patients()
        t_df = t_df.merge(p_df[["id", "nom", "prenom"]], left_on="patient_id", right_on="id", suffixes=("", "_p"))
        t_df["progress_pct"] = (t_df["progress"] * 100).round(0).astype(int)
        display = t_df[[
            "id", "nom", "prenom", "diagnostic", "type_prise_en_charge", "date_debut",
            "nb_seances_prevues", "seances_effectuees", "tarif_par_seance", "montant_du", "statut"
        ]].rename(columns={
            "id": "ID",
            "nom": "Nom",
            "prenom": "Prénom",
            "diagnostic": "Diagnostic",
            "type_prise_en_charge": "Prise en charge",
            "date_debut": "Début",
            "nb_seances_prevues": "Séances prévues",
            "seances_effectuees": "Séances faites",
            "tarif_par_seance": "Tarif (MAD)",
            "montant_du": "Reste dû (MAD)",
            "statut": "Statut"
        })
        st.dataframe(display, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("### ✏️ Modifier / Clôturer")
    if t_df.empty:
        st.caption("Ajoutez d'abord un traitement.")
        return

    tid = st.selectbox("Choisir un traitement par ID", t_df["id"].tolist())
    tr = t_df[t_df["id"] == tid].iloc[0]

    with st.form("form_edit_traitement"):
        diagnostic = st.text_input("Diagnostic / Motif", tr["diagnostic"] or "")
        tpec = st.text_input("Type de prise en charge", tr["type_prise_en_charge"] or "")
        date_debut = st.date_input("Date de début", to_ui_date(tr["date_debut"]) or date.today(), format="DD/MM/YYYY")
        nb_prev = st.number_input("Nombre de séances prévues", 1, 100, int(tr["nb_seances_prevues"]))
        tarif = st.number_input("Tarif par séance (MAD)", min_value=0.0, step=10.0, value=float(tr["tarif_par_seance"]))
        notes = st.text_area("Notes", tr["notes"] or "")
        statut = st.selectbox("Statut", ["En cours", "Terminé", "Archivé"], index=["En cours", "Terminé", "Archivé"].index(tr["statut"]))
        c1, c2, c3 = st.columns(3)
        if c1.form_submit_button("💾 Mettre à jour"):
            run_exec(
                "UPDATE traitements SET diagnostic=?, type_prise_en_charge=?, date_debut=?, nb_seances_prevues=?, tarif_par_seance=?, notes=?, statut=? WHERE id=?",
                (diagnostic.strip(), tpec.strip(), to_db_date(date_debut), int(nb_prev), float(tarif), notes.strip(), statut, tid),
            )
            clear_caches()
            st.success("Traitement mis à jour.")
            st_rerun()
        if c2.form_submit_button("🗑️ Supprimer", help="Supprime les séances associées"):
            run_exec("DELETE FROM traitements WHERE id=?", (tid,))
            clear_caches()
            st.success("Traitement supprimé.")
            st_rerun()
        if c3.form_submit_button("✅ Clôturer (Terminé)"):
            run_exec("UPDATE traitements SET statut='Terminé' WHERE id=?", (tid,))
            clear_caches()
            st.success("Traitement clôturé.")
            st_rerun()


def view_seances():
    st.subheader("🗓️ Séances")
    t_df = list_traitements(statut="En cours")
    if t_df.empty:
        st.info("Aucun traitement en cours. Créez d'abord un traitement.")
        return

    # Mapping affichage -> id
    patients_df = list_patients()
    t_df = t_df.merge(patients_df[["id", "nom", "prenom"]], left_on="patient_id", right_on="id", suffixes=("", "_p"))
    t_df["label"] = t_df.apply(lambda r: f"{r['nom']} {r['prenom']} · {r['type_prise_en_charge'] or r['diagnostic'] or 'Traitement'} (#{r['id']})", axis=1)
    tmap = {r["label"]: int(r["id"]) for _, r in t_df.iterrows()}

    with st.expander("➕ Planifier une séance", expanded=False):
        with st.form("form_add_seance", clear_on_submit=True):
            label = st.selectbox("Traitement *", list(tmap.keys()))
            d = st.date_input("Date *", format="DD/MM/YYYY")
            duree = st.number_input("Durée (minutes)", min_value=15, max_value=240, value=45)
            douleur_avant = st.slider("Douleur avant (0-10)", 0, 10, 5)
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Enregistrer")
            if submitted:
                run_exec(
                    "INSERT INTO seances (traitement_id, date, duree_minutes, douleur_avant, notes) VALUES (?, ?, ?, ?, ?)",
                    (
                        tmap[label],
                        to_db_date(d),
                        int(duree),
                        int(douleur_avant),
                        notes.strip(),
                    ),
                )
                clear_caches()
                st.success("Séance planifiée.")
                st_rerun()

    st.markdown("### 🔎 Filtrer")
    c1, c2, c3 = st.columns(3)
    with c1:
        label = st.selectbox("Traitement", ["Tous"] + list(tmap.keys()))
        tid = None if label == "Tous" else tmap[label]
    with c2:
        dmin = st.date_input("Du", value=date.today() - timedelta(days=30), format="DD/MM/YYYY")
    with c3:
        dmax = st.date_input("Au", value=date.today() + timedelta(days=30), format="DD/MM/YYYY")

    s_df = list_seances(traitement_id=tid, date_min=to_db_date(dmin), date_max=to_db_date(dmax))
    if s_df.empty:
        st.info("Aucune séance trouvée pour ce filtre.")
    else:
        s_df = s_df.sort_values("date")
        # Ajout infos patient
        t_all = list_traitements()
        p_all = list_patients()
        merged = s_df.merge(t_all[["id", "patient_id", "tarif_par_seance"]], left_on="traitement_id", right_on="id", suffixes=("", "_t")).merge(
            p_all[["id", "nom", "prenom", "telephone"]], left_on="patient_id", right_on="id", suffixes=("", "_p")
        )
        merged = merged.rename(columns={
            "date": "Date",
            "nom": "Nom",
            "prenom": "Prénom",
            "telephone": "Téléphone",
            "duree_minutes": "Durée (min)",
            "effectuee": "Effectuée",
            "payee": "Payée",
            "notes": "Notes",
            "tarif_par_seance": "Tarif (MAD)",
        })
        st.dataframe(
            merged[["Date", "Nom", "Prénom", "Téléphone", "Durée (min)", "Effectuée", "Payée", "Tarif (MAD)", "douleur_avant", "douleur_apres", "Notes"]],
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("---")
    st.markdown("### ✏️ Gérer une séance")
    if s_df.empty:
        st.caption("Planifiez d'abord une séance.")
        return

    sid = st.selectbox("Choisir une séance (ID)", s_df["id"].tolist())
    row = s_df[s_df["id"] == sid].iloc[0]
    with st.form("form_edit_seance"):
        d = st.date_input("Date", to_ui_date(row["date"]) or date.today(), format="DD/MM/YYYY")
        duree = st.number_input("Durée (minutes)", 15, 240, int(row["duree_minutes"]))
        effectuee = st.checkbox("Effectuée", value=bool(row["effectuee"]))
        payee = st.checkbox("Payée", value=bool(row["payee"]))
        douleur_avant = st.slider("Douleur avant (0-10)", 0, 10, int(row["douleur_avant"]) if row["douleur_avant"] is not None else 5)
        douleur_apres = st.slider("Douleur après (0-10)", 0, 10, int(row["douleur_apres"]) if row["douleur_apres"] is not None else 3)
        notes = st.text_area("Notes", row["notes"] or "")
        c1, c2 = st.columns(2)
        if c1.form_submit_button("💾 Mettre à jour"):
            run_exec(
                "UPDATE seances SET date=?, duree_minutes=?, effectuee=?, payee=?, douleur_avant=?, douleur_apres=?, notes=? WHERE id=?",
                (to_db_date(d), int(duree), int(effectuee), int(payee), int(douleur_avant), int(douleur_apres), notes.strip(), sid),
            )
            clear_caches()
            st.success("Séance mise à jour.")
            st_rerun()
        if c2.form_submit_button("🗑️ Supprimer"):
            run_exec("DELETE FROM seances WHERE id=?", (sid,))
            clear_caches()
            st.success("Séance supprimée.")
            st_rerun()


def view_exports():
    st.subheader("📤 Exports & Rapports")
    st.caption("Téléchargez des extractions CSV simples.")

    p_df = list_patients()
    t_df = list_traitements()
    s_df = list_seances()

    c1, c2, c3 = st.columns(3)
    with c1:
        st.write("Patients")
        csv = p_df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Export CSV", data=csv, file_name="patients.csv", mime="text/csv")
    with c2:
        st.write("Traitements")
        csv = t_df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Export CSV", data=csv, file_name="traitements.csv", mime="text/csv")
    with c3:
        st.write("Séances")
        csv = s_df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Export CSV", data=csv, file_name="seances.csv", mime="text/csv")

    st.markdown("---")
    st.markdown("### 📈 Indicateurs simples")
    if s_df.empty:
        st.info("Pas de données de séances.")
    else:
        total_faites = int(s_df["effectuee"].sum())
        total_payees = int(s_df["payee"].sum())
        st.write(f"Séances effectuées : **{total_faites}** · Séances payées : **{total_payees}**")
    

# ==========================
# Single hierarchical view
# ==========================

def _go_to(level: str, patient_id: int | None = None, traitement_id: int | None = None) -> None:
    """Helper to switch between hierarchical levels."""
    st.session_state["level"] = level
    st.session_state["current_patient_id"] = patient_id
    st.session_state["current_traitement_id"] = traitement_id
    st_rerun()


def render_patients():
    st.subheader("👤 Patients")
    if st.button("🏠 Tableau de bord"):
        _go_to("dashboard")
    search = st.text_input("Recherche (nom, prénom, téléphone)")
    df = list_patients(search)
    display_df = df[["nom", "prenom", "telephone", "email"]].copy()
    display_df.index = range(1, len(display_df) + 1)

    # Use row selection instead of a separate dropdown
    df_state = st.dataframe(
        display_df,
        use_container_width=True,
        key="patients_table",
        on_select="rerun",
        selection_mode="single-row",
    )

    # Update the current patient in session state when a row is clicked
    if df_state.selection.rows:
        st.session_state["current_patient_id"] = int(
            df.iloc[df_state.selection.rows[0]]["id"]
        )
    else:
        st.session_state["current_patient_id"] = None

    with st.expander("➕ Ajouter un patient", expanded=False):
        with st.form("form_add_patient_simple", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                nom = st.text_input("Nom *")
                prenom = st.text_input("Prénom")
                cin = st.text_input("CIN")
                telephone = st.text_input("Téléphone")
                email = st.text_input("Email")
            with c2:
                dtn = st.date_input("Date de naissance", min_value=date(1900, 1, 1), format="DD/MM/YYYY")
                adresse = st.text_area("Adresse")
                notes = st.text_area("Notes")
            if st.form_submit_button("Enregistrer"):
                if not nom.strip():
                    st.error("Le nom est obligatoire.")
                else:
                    run_exec(
                        "INSERT INTO patients (nom, prenom, cin, date_naissance, telephone, email, adresse, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (nom.strip(), prenom.strip(), cin.strip(), to_db_date(dtn), telephone.strip(), email.strip(), adresse.strip(), notes.strip()),
                    )
                    clear_caches()
                    st.success("Patient ajouté avec succès.")
                    st_rerun()

    if df.empty:
        st.info("Aucun patient trouvé.")

    pid = st.session_state.get("current_patient_id")
    row = df[df["id"] == pid].iloc[0] if pid is not None and not df.empty and not df[df["id"] == pid].empty else None

    with st.container(border=True):
        st.caption(
            f"Patient sélectionné : {row['nom']} {row['prenom']} - {row['telephone']} - {row['cin']}"
            if row is not None
            else "Patient sélectionné : Aucun",
        )

        with st.expander("✏️ Modifier / Supprimer", expanded=False):
            if row is None:
                st.info("Sélectionnez un patient pour modifier ou supprimer.")
            else:
                with st.form("form_edit_patient_simple"):
                    c1, c2 = st.columns(2)
                    with c1:
                        nom = st.text_input("Nom *", row["nom"])
                        prenom = st.text_input("Prénom", row["prenom"] or "")
                        cin = st.text_input("CIN", row["cin"] or "")
                        telephone = st.text_input("Téléphone", row["telephone"] or "")
                        email = st.text_input("Email", row["email"] or "")
                    with c2:
                        dtn = st.date_input(
                            "Date de naissance",
                            value=to_ui_date(row["date_naissance"]) or date(1990, 1, 1),
                            min_value=date(1900, 1, 1),
                            format="DD/MM/YYYY",
                        )
                        adresse = st.text_area("Adresse", row["adresse"] or "")
                        notes = st.text_area("Notes", row["notes"] or "")
                    c3, c4 = st.columns(2)
                    if c3.form_submit_button("💾 Mettre à jour"):
                        if not nom.strip():
                            st.error("Le nom est obligatoire.")
                        else:
                            run_exec(
                                "UPDATE patients SET nom=?, prenom=?, cin=?, date_naissance=?, telephone=?, email=?, adresse=?, notes=? WHERE id=?",
                                (nom.strip(), prenom.strip(), cin.strip(), to_db_date(dtn), telephone.strip(), email.strip(), adresse.strip(), notes.strip(), pid),
                            )
                            clear_caches()
                            st.success("Patient mis à jour.")
                            st_rerun()
                    if c4.form_submit_button("🗑️ Supprimer", help="Supprime également les traitements et séances associés"):
                        run_exec("DELETE FROM patients WHERE id=?", (pid,))
                        clear_caches()
                        st.success("Patient supprimé.")
                        st_rerun()

        if st.button("📋 Ouvrir les traitements du patient", disabled=row is None):
            _go_to("traitements", patient_id=pid)


def render_traitements():
    pid = st.session_state.get("current_patient_id")
    if pid is None:
        _go_to("patients")
        return
    p_df = list_patients()
    patient = p_df[p_df["id"] == pid].iloc[0]
    st.subheader(f"📝 Traitements – {patient['nom']} {patient['prenom']}")
    if st.button("⬅️ Retour aux patients"):
        _go_to("patients")

    t_df = list_traitements(patient_id=pid)
    display_t = t_df[["diagnostic", "type_prise_en_charge", "date_debut", "statut"]].copy()
    display_t.index = range(1, len(display_t) + 1)
    df_state = st.dataframe(
        display_t,
        use_container_width=True,
        key="traitements_table",
        on_select="rerun",
        selection_mode="single-row",
    )

    if df_state.selection.rows:
        st.session_state["current_traitement_id"] = int(
            t_df.iloc[df_state.selection.rows[0]]["id"]
        )
    else:
        st.session_state["current_traitement_id"] = None

    with st.expander("➕ Ajouter un traitement", expanded=False):
        with st.form("form_add_traitement_simple", clear_on_submit=True):
            diagnostic = st.text_input("Diagnostic / Motif")
            tpec = st.text_input("Type de prise en charge", placeholder="Ex: Lombalgie, Rééducation post-op, etc.")
            date_debut = st.date_input("Date de début", format="DD/MM/YYYY")
            nb_prev = st.number_input("Nombre de séances prévues", min_value=1, max_value=100, value=10)
            tarif = st.number_input("Tarif par séance (MAD)", min_value=0.0, step=10.0, value=0.0)
            notes = st.text_area("Notes")
            if st.form_submit_button("Enregistrer"):
                run_exec(
                    "INSERT INTO traitements (patient_id, diagnostic, type_prise_en_charge, date_debut, nb_seances_prevues, tarif_par_seance, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (pid, diagnostic.strip(), tpec.strip(), to_db_date(date_debut), int(nb_prev), float(tarif), notes.strip()),
                )
                clear_caches()
                st.success("Traitement ajouté.")
                st_rerun()

    if t_df.empty:
        st.info("Aucun traitement pour ce patient.")

    tid = st.session_state.get("current_traitement_id")
    row = (
        t_df[t_df["id"] == tid].iloc[0]
        if tid is not None and not t_df.empty and not t_df[t_df["id"] == tid].empty
        else None
    )

    with st.container(border=True):
        st.caption(
            f"Traitement sélectionné : {row['diagnostic']} - {row['date_debut']}"
            if row is not None
            else "Traitement sélectionné : Aucun"
        )

        with st.expander("✏️ Modifier / Supprimer", expanded=False):
            if row is None:
                st.info("Sélectionnez un traitement pour modifier ou supprimer.")
            else:
                with st.form("form_edit_traitement_simple"):
                    diagnostic = st.text_input("Diagnostic / Motif", row["diagnostic"] or "")
                    tpec = st.text_input("Type de prise en charge", row["type_prise_en_charge"] or "")
                    date_debut = st.date_input("Date de début", to_ui_date(row["date_debut"]) or date.today(), format="DD/MM/YYYY")
                    nb_prev = st.number_input("Nombre de séances prévues", 1, 100, int(row["nb_seances_prevues"]))
                    tarif = st.number_input("Tarif par séance (MAD)", min_value=0.0, step=10.0, value=float(row["tarif_par_seance"]))
                    notes = st.text_area("Notes", row["notes"] or "")
                    statut = st.selectbox("Statut", ["En cours", "Terminé", "Archivé"], index=["En cours", "Terminé", "Archivé"].index(row["statut"]))
                    c1, c2 = st.columns(2)
                    if c1.form_submit_button("💾 Mettre à jour"):
                        run_exec(
                            "UPDATE traitements SET diagnostic=?, type_prise_en_charge=?, date_debut=?, nb_seances_prevues=?, tarif_par_seance=?, notes=?, statut=? WHERE id=?",
                            (diagnostic.strip(), tpec.strip(), to_db_date(date_debut), int(nb_prev), float(tarif), notes.strip(), statut, tid),
                        )
                        clear_caches()
                        st.success("Traitement mis à jour.")
                        st_rerun()
                    if c2.form_submit_button("🗑️ Supprimer", help="Supprime les séances associées"):
                        run_exec("DELETE FROM traitements WHERE id=?", (tid,))
                        clear_caches()
                        st.success("Traitement supprimé.")
                        st_rerun()

        if st.button("🗓️ Ouvrir les séances du traitement", disabled=row is None):
            _go_to("seances", patient_id=pid, traitement_id=tid)


def render_seances():
    tid = st.session_state.get("current_traitement_id")
    pid = st.session_state.get("current_patient_id")
    if tid is None or pid is None:
        _go_to("patients")
        return
    t_df = list_traitements(patient_id=pid)
    tr = t_df[t_df["id"] == tid].iloc[0]
    st.subheader("🗓️ Séances")
    if st.button("⬅️ Retour aux traitements"):
        _go_to("traitements", patient_id=pid)

    s_df = list_seances(traitement_id=tid)
    display_s = s_df[["date", "heure", "duree_minutes", "cout", "effectuee", "payee", "notes"]].copy()
    display_s = display_s.rename(columns={"notes": "Note"})
    display_s.index = range(1, len(display_s) + 1)
    df_state = st.dataframe(
        display_s,
        use_container_width=True,
        key="seances_table",
        on_select="rerun",
        selection_mode="single-row",
    )

    if df_state.selection.rows:
        st.session_state["current_seance_id"] = int(
            s_df.iloc[df_state.selection.rows[0]]["id"]
        )
    else:
        st.session_state["current_seance_id"] = None

    with st.expander("➕ Planifier une séance", expanded=False):
        with st.form("form_add_seance_simple", clear_on_submit=True):
            d = st.date_input("Date *", format="DD/MM/YYYY")
            h = st.time_input("Heure", value=time(10, 0))
            duree = st.number_input("Durée (minutes)", min_value=15, max_value=240, value=45)
            cout = st.number_input("Coût (MAD)", min_value=0.0, step=10.0, value=float(tr["tarif_par_seance"]))
            notes = st.text_area("Note")
            if st.form_submit_button("Enregistrer"):
                run_exec(
                    "INSERT INTO seances (traitement_id, date, heure, duree_minutes, cout, notes) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        tid,
                        to_db_date(d),
                        to_db_time(h),
                        int(duree),
                        float(cout),
                        notes.strip(),
                    ),
                )
                clear_caches()
                st.success("Séance planifiée.")
                st_rerun()

    if s_df.empty:
        st.info("Aucune séance pour ce traitement.")

    sid = st.session_state.get("current_seance_id")
    row = (
        s_df[s_df["id"] == sid].iloc[0]
        if sid is not None and not s_df.empty and not s_df[s_df["id"] == sid].empty
        else None
    )
    with st.container(border=True):
        st.caption(
            f"Séance sélectionnée : {row['date']} {row['heure'] or ''}"
            if row is not None
            else "Séance sélectionnée : Aucune"
        )
        with st.expander("✏️ Modifier / Supprimer", expanded=False):
            if row is None:
                st.info("Sélectionnez une séance pour modifier ou supprimer.")
            else:
                with st.form("form_edit_seance_simple"):
                    d = st.date_input("Date", to_ui_date(row["date"]) or date.today(), format="DD/MM/YYYY")
                    h = st.time_input("Heure", to_ui_time(row["heure"]) or time(10, 0))
                    duree = st.number_input("Durée (minutes)", 15, 240, int(row["duree_minutes"]))
                    cout = st.number_input(
                        "Coût (MAD)",
                        min_value=0.0,
                        step=10.0,
                        value=float(row["cout"]) if row["cout"] is not None else float(tr["tarif_par_seance"]),
                    )
                    effectuee = st.checkbox("Effectuée", value=bool(row["effectuee"]))
                    payee = st.checkbox("Payée", value=bool(row["payee"]))
                    notes = st.text_area("Note", row["notes"] or "")
                    c1, c2 = st.columns(2)
                    if c1.form_submit_button("💾 Mettre à jour"):
                        run_exec(
                            "UPDATE seances SET date=?, heure=?, duree_minutes=?, cout=?, effectuee=?, payee=?, notes=? WHERE id=?",
                            (
                                to_db_date(d),
                                to_db_time(h),
                                int(duree),
                                float(cout),
                                int(effectuee),
                                int(payee),
                                notes.strip(),
                                sid,
                            ),
                        )
                        clear_caches()
                        st.success("Séance mise à jour.")
                        st_rerun()
                    if c2.form_submit_button("🗑️ Supprimer"):
                        run_exec("DELETE FROM seances WHERE id=?", (sid,))
                        clear_caches()
                        st.success("Séance supprimée.")
                        st_rerun()


def view_manager():
    if "level" not in st.session_state:
        st.session_state["level"] = "dashboard"
        st.session_state["current_patient_id"] = None
        st.session_state["current_traitement_id"] = None
        st.session_state["current_seance_id"] = None
    level = st.session_state.get("level", "dashboard")
    if level == "dashboard":
        render_dashboard()
    elif level == "patients":
        render_patients()
    elif level == "traitements":
        render_traitements()
    else:
        render_seances()

# ==========================
# App Entry
# ==========================
init_db()
view_manager()
