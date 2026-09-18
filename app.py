"""
Appli web : Production RTE par groupe de production.

Affiche les données de l'API "Actual Generation" de RTE
(ressource actual_generations_per_unit) dans une interface web simple :
sélection de période, filtre par filière, graphique, tableau, export CSV.

Lancement en local :
    streamlit run app.py

Déploiement : voir README.md (Streamlit Community Cloud, gratuit).
Les identifiants RTE sont lus depuis st.secrets (jamais exposés au navigateur).
"""

import base64
import colorsys
import datetime as dt
import re
import time

import numpy as np
import pandas as pd
import requests
import streamlit as st
import altair as alt

TOKEN_URL = "https://digital.iservices.rte-france.com/token/oauth/"
BASE_URL = "https://digital.iservices.rte-france.com/open_api/actual_generation/v1"
RESOURCE = "actual_generations_per_unit"
CHUNK_DAYS = 7

# Traduction des filières RTE en français (pour l'affichage)
FILIERES_FR = {
    "NUCLEAR": "Nucléaire",
    "HYDRO": "Hydraulique",
    "HYDRO_PUMPED_STORAGE": "Hydraulique (STEP)",
    "HYDRO_RUN_OF_RIVER_AND_POUNDAGE": "Hydraulique (fil de l'eau)",
    "HYDRO_WATER_RESERVOIR": "Hydraulique (lac)",
    "GAS": "Gaz",
    "FOSSIL_GAS": "Gaz",
    "COAL": "Charbon",
    "FOSSIL_HARD_COAL": "Charbon",
    "OIL": "Fioul",
    "FOSSIL_OIL": "Fioul",
    "WIND": "Éolien",
    "WIND_ONSHORE": "Éolien terrestre",
    "WIND_OFFSHORE": "Éolien en mer",
    "SOLAR": "Solaire",
    "BIOMASS": "Biomasse",
    "BIOENERGY": "Bioénergies",
    "WASTE": "Déchets",
}

# Couleurs inspirées de la palette éCO2mix de RTE, par grande filière.
# (clé = "famille" affichée ; valeur = code couleur hexadécimal)
ECO2MIX_COLORS = {
    "Nucléaire":   "#f5a623",  # orange
    "Gaz":         "#d0463b",  # rouge
    "Charbon":     "#5b4a42",  # brun foncé
    "Fioul":       "#8d6e63",  # brun clair
    "Hydraulique": "#2e8fd4",  # bleu
    "Pompage":     "#23467a",  # bleu foncé (STEP / pompage)
    "Éolien":      "#3ec1c9",  # turquoise
    "Solaire":     "#f4d03f",  # jaune
    "Bioénergies": "#3f9b5b",  # vert
    "Autre":       "#9aa0a6",  # gris
}
# Ordre d'affichage des boutons / légende
FAMILLE_ORDER = list(ECO2MIX_COLORS.keys())


def famille_from_type(production_type: str) -> str:
    """Regroupe le type RTE détaillé en grande filière (clé de ECO2MIX_COLORS)."""
    pt = (production_type or "").upper()
    if "NUCLEAR" in pt:
        return "Nucléaire"
    if "PUMP" in pt:                       # HYDRO_PUMPED_STORAGE
        return "Pompage"
    if "HYDRO" in pt:
        return "Hydraulique"
    if "WIND" in pt:
        return "Éolien"
    if "SOLAR" in pt:
        return "Solaire"
    if "GAS" in pt:
        return "Gaz"
    if "COAL" in pt or "LIGNITE" in pt:
        return "Charbon"
    if "OIL" in pt or "FUEL" in pt:
        return "Fioul"
    if any(k in pt for k in ("BIO", "WASTE", "BIOMASS")):
        return "Bioénergies"
    return "Autre"


# Altair : on autorise un grand nombre de points (plusieurs centrales x temps).
alt.data_transformers.disable_max_rows()


# ----------------------------------------------------------------------------
# Récap du parc nucléaire par cours d'eau (données statiques, style carte EDF)
# ----------------------------------------------------------------------------
# Couleur de la tour aéroréfrigérante selon la puissance des réacteurs
PUISSANCE_COULEURS = {
    900: "#2f7ed8",    # bleu
    1300: "#6fbf44",   # vert
    1450: "#c25ec4",   # rose
    1650: "#e8542f",   # rouge (EPR)
}

# Centrales par cours d'eau (ordre amont -> aval), + catégorie eau de mer.
# Chaque entrée : (nom, nombre de réacteurs, puissance unitaire MWe)
PARC_NUCLEAIRE = {
    "💧 Loire": [("Belleville", 2, 1300), ("Dampierre", 4, 900),
                 ("Saint-Laurent", 2, 900), ("Chinon", 4, 900)],
    "💧 Vienne": [("Civaux", 2, 1450)],
    "💧 Rhône": [("Bugey", 4, 900), ("Saint-Alban", 2, 1300),
                 ("Cruas", 4, 900), ("Tricastin", 4, 900)],
    "💧 Garonne": [("Golfech", 2, 1300), ("Le Blayais", 4, 900)],
    "💧 Seine": [("Nogent-sur-Seine", 2, 1300)],
    "💧 Moselle": [("Cattenom", 4, 1300)],
    "💧 Meuse": [("Chooz", 2, 1450)],
    "🌊 Eau de mer": [("Gravelines", 6, 900), ("Penly", 2, 1300),
                      ("Paluel", 4, 1300), ("Flamanville", 2, 1300),
                      ("Flamanville EPR", 1, 1650)],
}


def _tour_svg(couleur: str, taille: int = 22) -> str:
    """Petite tour aéroréfrigérante en SVG, avec panache de vapeur."""
    hauteur = round(taille * 26 / 20)
    return (
        f'<svg width="{taille}" height="{hauteur}" viewBox="0 0 20 26" style="flex:none">'
        f'<circle cx="13.5" cy="3.5" r="2.8" fill="#e3e9ee"/>'
        f'<path d="M5.5 7 L14.5 7 C13.8 11 14.8 17 17.5 25 L2.5 25 '
        f'C5.2 17 6.2 11 5.5 7 Z" fill="{couleur}" stroke="rgba(0,0,0,0.15)"/>'
        f'<ellipse cx="10" cy="7" rx="4.5" ry="1.5" fill="#dfe6ec"/>'
        f'</svg>'
    )


def _badge(n: int) -> str:
    # couleur du texte explicite : en thème sombre, le texte hériterait du
    # blanc et deviendrait invisible sur le fond blanc du badge
    return (
        '<span style="border:1.5px solid #7d8899;border-radius:4px;background:#fff;'
        'color:#111827;font-weight:700;font-size:0.8rem;min-width:20px;'
        f'text-align:center;padding:0 3px">{n}</span>'
    )


def render_parc_nucleaire():
    """Affiche la récap du parc nucléaire par rivière / eau de mer."""
    st.subheader("Le parc nucléaire par cours d'eau")
    st.caption(
        "Chaque centrale est refroidie par une rivière ou par la mer. "
        "Couleur de la tour = puissance des réacteurs ; le chiffre = nombre "
        "de réacteurs du site."
    )
    cartes = []
    for cours_eau, centrales in PARC_NUCLEAIRE.items():
        lignes = "".join(
            '<div style="display:flex;align-items:center;gap:7px;padding:2px 0">'
            f'{_tour_svg(PUISSANCE_COULEURS[mw])}'
            f'<span style="font-size:0.86rem;flex:1;white-space:nowrap;color:#1f2937">{nom}</span>'
            f'{_badge(n)}</div>'
            for nom, n, mw in centrales
        )
        cartes.append(
            '<div style="border:1px solid #dde5ec;border-radius:10px;'
            'padding:8px 14px;background:#f8fbfd;min-width:170px">'
            f'<div style="font-weight:700;color:#1e4a72;margin-bottom:4px">{cours_eau}</div>'
            f'{lignes}</div>'
        )
    legende_items = "".join(
        f'<span style="display:flex;align-items:center;gap:5px">{_tour_svg(c, 16)} '
        f'{mw} MWe{" (EPR)" if mw == 1650 else ""}</span>'
        for mw, c in PUISSANCE_COULEURS.items()
    )
    html = (
        '<div style="display:flex;flex-wrap:wrap;gap:10px;align-items:stretch">'
        + "".join(cartes) + '</div>'
        '<div style="display:flex;flex-wrap:wrap;gap:18px;margin-top:12px;'
        'padding:8px 12px;background:#f4f7fa;border-radius:8px;font-size:0.82rem;'
        'align-items:center;color:#1f2937">'
        '<span style="font-weight:700">Puissance des réacteurs :</span>'
        + legende_items
        + f'<span style="display:flex;align-items:center;gap:5px">{_badge(4)} '
        'nombre de réacteurs du site</span></div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def _text_color(hex_color: str) -> str:
    """Noir ou blanc selon la luminosité du fond, pour rester lisible."""
    r, g, b = (int(hex_color[k:k + 2], 16) for k in (1, 3, 5))
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6 else "#ffffff"


def _hsl_to_hex(hue_deg: float, s: float, l: float) -> str:
    r, g, b = colorsys.hls_to_rgb((hue_deg % 360) / 360, l, s)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


# Couleurs des courbes : une couleur usuelle par centrale (site), déclinée du
# clair au foncé pour ses tranches. Chaque liste est parcourue dans l'ordre
# alphabétique des centrales affichées, et reprend au début si elle s'épuise.
# Nucléaire en bord de mer : le bleu de la mer, le jaune et le marron du sable
COULEURS_MER = [
    "#1e88e5",  # bleu
    "#fdd835",  # jaune
    "#9c6b30",  # marron
    "#26c6da",  # bleu turquoise
]
# Nucléaire en bord de rivière : des verts et des rouges, en alternance
COULEURS_RIVIERE = [
    "#2ca02c",  # vert
    "#e53935",  # rouge
    "#8bc34a",  # vert pomme
    "#d81b60",  # rouge framboise
    "#2ecc71",  # vert émeraude
    "#f4511e",  # rouge orangé
]
# Autres filières : d'abord des couleurs absentes du nucléaire, puis celles du
# nucléaire que les centrales affichées n'utilisent pas
COULEURS_AUTRES = [
    "#8c47d1",  # violet
    "#fb8c00",  # orange
    "#e040fb",  # fuchsia
    "#9e9e9e",  # gris
    "#3f51b5",  # indigo
]
# Centrales nucléaires refroidies par la mer, sous leur nom RTE (catégorie
# « Eau de mer » de PARC_NUCLEAIRE) ; les autres sont en bord de rivière
SITES_BORD_DE_MER = {"FLAMANVILLE", "GRAVELINES", "PALUEL", "PENLY"}
# Écart de luminosité entre la couleur de la centrale et ses tranches
# extrêmes : la plus claire à +0,18, la plus foncée à -0,18
ECART_TRANCHES = 0.18


def _site_tranche(nom: str):
    """Sépare le nom RTE d'un groupe en centrale et numéro de tranche :
    "ST ALBAN 1" -> ("ST ALBAN", 1), "DK6-TG1" -> ("DK6-TG", 1). Un nom
    sans numéro final forme à lui seul sa centrale, avec None pour numéro."""
    m = re.fullmatch(r"(.*?)[\s-]*(\d+)", nom.strip())
    if m and m.group(1):
        return m.group(1), int(m.group(2))
    return nom.strip(), None


def _cle_naturelle(nom: str):
    """Clé de tri par centrale puis par numéro de tranche, pour ranger
    BATHIE 2 avant BATHIE 10."""
    site, numero = _site_tranche(nom)
    return site, -1 if numero is None else numero


def _degrade(base: str, n: int) -> list:
    """n nuances d'une couleur, de la plus claire à la plus foncée, réparties
    autour de sa luminosité : la centrale se reconnaît à sa teinte, chaque
    tranche à sa nuance. Une centrale d'une seule tranche garde la couleur
    telle quelle. La plus foncée ne descend pas sous 0,34 de luminosité pour
    rester visible sur fond noir."""
    if n == 1:
        return [base]
    r, g, b = (int(base[k:k + 2], 16) / 255 for k in (1, 3, 5))
    teinte, luminosite, saturation = colorsys.rgb_to_hls(r, g, b)
    fonce = min(max(luminosite - ECART_TRANCHES, 0.34), 0.82 - 2 * ECART_TRANCHES)
    return [
        _hsl_to_hex(teinte * 360, saturation,
                    fonce + 2 * ECART_TRANCHES * (1 - i / (n - 1)))
        for i in range(n)
    ]


def build_color_map(df_sel: pd.DataFrame) -> dict:
    """Associe à chaque groupe une couleur usuelle : une couleur par centrale,
    déclinée du clair au foncé pour ses tranches, dans l'ordre de leur
    numéro. Les centrales nucléaires de bord de mer sont en bleu, jaune ou
    marron, celles de rivière en vert ou rouge, les autres filières prennent
    les couleurs restantes.

    Les clés sont rangées par centrale puis par numéro de tranche : c'est
    l'ordre de la légende des courbes."""
    tranches, familles = {}, {}
    for groupe, famille in (df_sel[["groupe", "famille"]]
                            .drop_duplicates("groupe").itertuples(index=False)):
        site, numero = _site_tranche(groupe)
        tranches.setdefault(site, []).append((-1 if numero is None else numero, groupe))
        familles.setdefault(site, famille)

    def categorie(site):
        if familles[site] != "Nucléaire":
            return "autre"
        return "mer" if site in SITES_BORD_DE_MER else "riviere"

    sites = sorted(tranches)
    base_site = {}
    # Le nucléaire d'abord, pour savoir quelles couleurs restent aux autres
    for cat, palette in (("mer", COULEURS_MER), ("riviere", COULEURS_RIVIERE)):
        for i, site in enumerate(s for s in sites if categorie(s) == cat):
            base_site[site] = palette[i % len(palette)]
    restantes = COULEURS_AUTRES + [c for c in COULEURS_MER + COULEURS_RIVIERE
                                   if c not in base_site.values()]
    for i, site in enumerate(s for s in sites if categorie(s) == "autre"):
        base_site[site] = restantes[i % len(restantes)]

    couleurs = {}
    for site, base in base_site.items():
        groupes = [g for _, g in sorted(tranches[site])]
        couleurs.update(zip(groupes, _degrade(base, len(groupes))))
    return {g: couleurs[g] for g in sorted(couleurs, key=_cle_naturelle)}


def line_chart_by_group(data: pd.DataFrame, color_map: dict, x_title: str,
                        height: int = 420, x_format: str = "%d/%m %Hh"):
    """Graphique en courbes : une tranche = une courbe, légende = nom des
    tranches, couleurs = une par centrale, nuancée par tranche (voir
    build_color_map).

    Une couche invisible mais épaisse est superposée à chaque courbe
    (mark_line strokeWidth=20, opacity=0) pour élargir la zone de survol :
    il n'est plus nécessaire de viser précisément le trait fin pour faire
    apparaître l'infobulle.

    `x_format` (format d3-time) est explicite plutôt que laissé à l'auto-
    formatage d'Altair : sur une plage de plusieurs jours, celui-ci n'affiche
    parfois que l'heure sur les graduations, ce qui rend impossible de situer
    les points dans le temps."""
    domaine = [g for g in color_map if g in data["groupe"].unique()]
    plage = [color_map[g] for g in domaine]
    couleur = alt.Color(
        "groupe:N", title="Centrale",
        scale=alt.Scale(domain=domaine, range=plage),
        legend=alt.Legend(symbolType="stroke", labelLimit=260),
    )
    encodage_commun = dict(
        x=alt.X("debut:T", title=x_title, axis=alt.Axis(format=x_format, labelAngle=-45)),
        y=alt.Y("valeur_mw:Q", title="Production (MW)"),
    )
    tooltip = [
        alt.Tooltip("groupe:N", title="Centrale"),
        alt.Tooltip("famille:N", title="Filière"),
        alt.Tooltip("debut:T", title="Date"),
        alt.Tooltip("valeur_mw:Q", title="MW", format=".0f"),
    ]
    trait_visible = alt.Chart(data).mark_line(strokeWidth=1.5).encode(
        color=couleur, **encodage_commun,
    )
    zone_survol = alt.Chart(data).mark_line(strokeWidth=20, opacity=0).encode(
        color=alt.Color("groupe:N", scale=alt.Scale(domain=domaine, range=plage), legend=None),
        tooltip=tooltip,
        **encodage_commun,
    )
    return _fond_transparent(
        (trait_visible + zone_survol).properties(height=height).interactive()
    )


def area_chart_by_group(data: pd.DataFrame, color_map: dict, x_title: str,
                        height: int = 420, x_format: str = "%d/%m %Hh"):
    """Graphique en aires empilées : les productions des centrales
    s'additionnent, chaque centrale gardant la couleur de sa courbe.

    Les centrales sont empilées par filière dans l'ordre éCO2mix (le
    nucléaire posé sur l'axe, comme sur éCO2mix), puis par nom. La légende
    suit l'empilement : sa première ligne est la couche du haut, sa dernière
    celle du bas, pour retrouver d'un coup d'œil quelle aire correspond à
    quelle centrale.

    Seules les centrales présentes dans `color_map` sont gardées, comme sur
    les courbes : une centrale sans couleur y reste invisible, mais ici elle
    creuserait un vide dans la pile."""
    data = data[data["groupe"].isin(list(color_map))]
    famille = data.drop_duplicates("groupe").set_index("groupe")["famille"]
    rang_famille = {f: i for i, f in enumerate(FAMILLE_ORDER)}
    # Ordre de la légende, de haut en bas : les filières de la dernière à la
    # première (le nucléaire finit en bas), puis les centrales par nom et
    # leurs tranches par numéro, donc de la plus claire à la plus foncée
    domaine = sorted(
        famille.index,
        key=lambda g: (-rang_famille.get(famille[g], len(FAMILLE_ORDER)), _cle_naturelle(g)),
    )
    plage = [color_map[g] for g in domaine]
    # Rang d'empilement : 0 = couche posée sur l'axe = dernière ligne de la légende
    rang = {g: len(domaine) - 1 - i for i, g in enumerate(domaine)}
    data = data.assign(rang=data["groupe"].map(rang))
    graphe = alt.Chart(data).mark_area().encode(
        x=alt.X("debut:T", title=x_title, axis=alt.Axis(format=x_format, labelAngle=-45)),
        y=alt.Y("valeur_mw:Q", title="Production cumulée (MW)", stack="zero"),
        color=alt.Color(
            "groupe:N", title="Centrale",
            scale=alt.Scale(domain=domaine, range=plage),
            legend=alt.Legend(symbolType="square", labelLimit=260),
        ),
        order=alt.Order("rang:Q", sort="ascending"),
        tooltip=[
            alt.Tooltip("groupe:N", title="Centrale"),
            alt.Tooltip("famille:N", title="Filière"),
            alt.Tooltip("debut:T", title="Date"),
            alt.Tooltip("valeur_mw:Q", title="MW", format=".0f"),
        ],
    )
    return _fond_transparent(graphe.properties(height=height).interactive())


# Grille des graphiques : ce gris semi-transparent redonne sur fond noir la
# teinte du thème Streamlit (#31333F), et reste visible sur le fond gris de la
# section du mois glissant, où cette teinte se confondrait avec le fond.
GRILLE_TRANSPARENTE = "rgba(128, 128, 128, 0.31)"


def _fond_transparent(graphe):
    """Laisse voir le fond de la section (noir ou gris) derrière le graphique :
    le thème Streamlit y peint sinon la couleur de fond de la page."""
    return (graphe.properties(background="transparent")
            .configure_axis(gridColor=GRILLE_TRANSPARENTE))


def graphe_cumule(data: pd.DataFrame, color_map: dict, **options):
    """Titre et graphique en aires empilées, placés sous un graphique en
    courbes. `options` : mêmes réglages d'axe que pour les courbes."""
    st.markdown(
        "##### Production cumulée",
        help="Les productions des centrales s'additionnent : le haut de la pile "
             "donne la production totale des centrales affichées. La légende "
             "suit l'empilement, de la couche du haut à celle du bas.",
    )
    st.altair_chart(area_chart_by_group(data, color_map, **options),
                    use_container_width=True)


def fenetre_glissante(client_id, client_secret, jours: int):
    """Charge les données des `jours` derniers jours jusqu'à aujourd'hui inclus."""
    fin = dt.date.today() + dt.timedelta(days=1)   # inclut la journée en cours
    debut = dt.date.today() - dt.timedelta(days=jours)
    return load_data(client_id, client_secret, debut, fin)


# ----------------------------------------------------------------------------
# Accès API (côté serveur, le secret reste caché)
# ----------------------------------------------------------------------------
# À propos du cache (@st.cache_data)
# ----------------------------------
# Streamlit ré-exécute TOUT le script à chaque interaction (clic, filtre,
# changement de date). Sans cache, chaque clic relancerait les appels à l'API
# RTE : lenteur, et risque de dépasser les quotas de l'API.
#
# @st.cache_data mémorise le résultat d'une fonction pour un jeu d'arguments
# donné. Deux propriétés importantes ici :
#   - le cache est PARTAGÉ ENTRE TOUS LES VISITEURS. Si quelqu'un charge les
#     7 derniers jours, le visiteur suivant qui demande la même période reçoit
#     la donnée instantanément, sans nouvel appel à RTE ;
#   - `ttl` fixe la durée de validité (au-delà, la donnée est rechargée), et
#     `max_entries` plafonne le nombre de résultats conservés, les plus anciens
#     étant supprimés en premier (LRU).
#
# `max_entries` est indispensable ici : une plage de 90 jours représente plus
# d'un million de lignes, soit plusieurs centaines de Mo en mémoire. Sans
# plafond, le cache grossit à chaque période différente demandée par les
# visiteurs jusqu'à dépasser la limite mémoire de l'hébergement (~1 Go sur
# Streamlit Community Cloud), ce qui fait redémarrer l'application.
# ----------------------------------------------------------------------------


def get_credentials():
    """Récupère client_id / client_secret depuis les secrets Streamlit."""
    try:
        return st.secrets["RTE_CLIENT_ID"], st.secrets["RTE_CLIENT_SECRET"]
    except Exception:
        return None, None


# Le token RTE est valable ~2h. Un seul jeu d'identifiants : 2 entrées suffisent.
@st.cache_data(ttl=6000, max_entries=2, show_spinner=False)
def get_token(client_id: str, client_secret: str) -> str:
    creds = f"{client_id}:{client_secret}".encode("utf-8")
    b64 = base64.b64encode(creds).decode("utf-8")
    resp = requests.post(
        TOKEN_URL, headers={"Authorization": f"Basic {b64}"}, timeout=30
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _paris_offset(date: dt.date) -> str:
    def last_sunday(year, month):
        nxt = dt.date(year + (month == 12), (month % 12) + 1, 1)
        last = nxt - dt.timedelta(days=1)
        return last - dt.timedelta(days=(last.weekday() + 1) % 7)
    return "+02:00" if last_sunday(date.year, 3) <= date < last_sunday(date.year, 10) else "+01:00"


def _iso(date: dt.date) -> str:
    return f"{date.isoformat()}T00:00:00{_paris_offset(date)}"


def fetch_window(token, start, end):
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{BASE_URL}/{RESOURCE}?start_date={_iso(start)}&end_date={_iso(end)}"
    for _ in range(5):
        resp = requests.get(url, headers=headers, timeout=60)
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", "30")))
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("Quota API dépassé, réessayez plus tard.")


def flatten(payload):
    rows = []
    for unit in payload.get(RESOURCE, []):
        info = unit.get("unit", {})
        for v in unit.get("values", []):
            rows.append({
                "code_eic": info.get("eic_code", ""),
                "groupe": info.get("name", ""),
                "filiere": info.get("production_type", ""),
                "debut": v.get("start_date", ""),
                "valeur_mw": v.get("value", None),
            })
    return rows


# Cache partagé par tous les visiteurs : la même période n'est téléchargée
# qu'une fois pour tout le monde pendant 30 min. `max_entries=6` borne la
# mémoire : au 7ᵉ jeu de dates différent, le plus ancien est évincé.
@st.cache_data(ttl=1800, max_entries=6, show_spinner=False)
def load_data(client_id, client_secret, start: dt.date, end: dt.date) -> pd.DataFrame:
    token = get_token(client_id, client_secret)
    rows = []
    cur = start
    while cur < end:
        nxt = min(cur + dt.timedelta(days=CHUNK_DAYS), end)
        rows.extend(flatten(fetch_window(token, cur, nxt)))
        cur = nxt
        time.sleep(0.5)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["debut"] = pd.to_datetime(df["debut"])
        df["filiere_fr"] = df["filiere"].map(FILIERES_FR).fillna(df["filiere"])
        df["famille"] = df["filiere"].map(famille_from_type)
    return df


CAPACITIES_URL = (
    "https://digital.iservices.rte-france.com/open_api/"
    "generation_installed_capacities/v1/capacities_per_production_unit"
)


# Les puissances installées ne bougent quasiment jamais : 24h de cache.
@st.cache_data(ttl=86400, max_entries=2, show_spinner=False)
def load_installed_capacities(client_id, client_secret) -> dict:
    """Renvoie {code_eic: puissance_installée_MW}.
    Nécessite l'abonnement à l'API 'Generation Installed Capacities'.
    Renvoie {} si l'API est indisponible (pas d'abonnement, etc.)."""
    try:
        token = get_token(client_id, client_secret)
        r = requests.get(CAPACITIES_URL,
                         headers={"Authorization": f"Bearer {token}"}, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return {}
    cap = {}
    for item in data.get("capacities_per_production_unit", []):
        eic = item.get("eic_code") or ""
        val = item.get("installed_capacity")
        if val is None:                      # parfois dans une liste 'values'
            vals = item.get("values") or []
            if vals:
                val = vals[-1].get("value", vals[-1].get("installed_capacity"))
        if val is None:
            val = item.get("value")
        if eic and val is not None:
            cap[eic] = val
    return cap


def build_table(df: pd.DataFrame, cap_dict: dict):
    """Construit le tableau par centrale : énergie, puissance installée,
    Pmax/Pmin sur la plage, et énergie journalière (GWh) par jour."""
    # Pas de mesure (en heures) estimé à partir de l'écart médian entre points
    diffs = df.sort_values("debut").groupby("groupe")["debut"].diff().dropna()
    step_h = (diffs.dt.total_seconds().median() / 3600) if not diffs.empty else 1.0

    tmp = df.copy()
    tmp["jour"] = tmp["debut"].dt.date
    tmp["mwh"] = tmp["valeur_mw"] * step_h

    agg = (
        tmp.groupby(["groupe", "code_eic", "famille"])
        .agg(pmax=("valeur_mw", "max"), pmin=("valeur_mw", "min"))
        .reset_index()
    )
    agg["installee"] = agg["code_eic"].map(cap_dict)

    # Énergie journalière en GWh, une colonne par jour
    daily = (tmp.groupby(["groupe", "jour"])["mwh"].sum() / 1000).unstack("jour")
    daily = daily.reindex(sorted(daily.columns), axis=1)

    # Modulation : énergie du dernier jour de la plage / max de la plage
    if daily.shape[1]:
        dernier_jour = daily.iloc[:, -1]
        max_plage = daily.max(axis=1)
        modulation = (dernier_jour / max_plage).replace([np.inf, -np.inf], np.nan)
    else:
        modulation = pd.Series(dtype=float)
    agg["modulation"] = agg["groupe"].map(modulation)

    daily.columns = [pd.Timestamp(c).strftime("%d/%m") + " (GWh)" for c in daily.columns]
    daily = daily.reset_index()

    table = agg.merge(daily, on="groupe", how="left").rename(columns={
        "groupe": "Centrale", "famille": "Énergie",
        "installee": "Puissance installée (MW)",
        "pmax": "Pmax plage (MW)", "pmin": "Pmin plage (MW)",
        "modulation": "Modulation",
    })
    jour_cols = [c for c in table.columns if c.endswith("(GWh)")]
    ordre = ["Centrale", "Énergie", "Puissance installée (MW)",
             "Pmax plage (MW)", "Pmin plage (MW)", "Modulation"] + jour_cols
    return table[ordre], step_h, jour_cols


def render_table(df, client_id, client_secret):
    """Vue 'Tableaux' : tableau filtrable, coloré selon la palette éCO2mix."""
    st.subheader("Tableau des centrales")

    cap = load_installed_capacities(client_id, client_secret)
    if not cap:
        st.info(
            "La puissance installée n'a pas pu être chargée. Pour l'afficher, abonne "
            "ton application RTE à l'API « Generation Installed Capacities » (même "
            "client_id / client_secret). En attendant, la colonne affiche « n.d. »."
        )

    table, step_h, jour_cols = build_table(df, cap)

    # --- Filtres (style tableur) ---
    familles_present = [f for f in FAMILLE_ORDER if f in table["Énergie"].unique()]
    c1, c2 = st.columns([3, 1])
    with c1:
        fam_sel = st.multiselect("Filtrer par énergie", familles_present,
                                 default=familles_present)
    with c2:
        seuil_inst = st.number_input("Puiss. installée min. (MW)",
                                     min_value=0, value=0, step=50)

    table = table[table["Énergie"].isin(fam_sel)]
    if seuil_inst > 0:
        table = table[table["Puissance installée (MW)"].fillna(0) >= seuil_inst]
    if table.empty:
        st.warning("Aucune centrale ne correspond aux filtres.")
        return

    # --- Coloration des lignes selon la filière (palette éCO2mix) ---
    def style_row(row):
        couleur = ECO2MIX_COLORS.get(row["Énergie"], "#ffffff")
        styles = [""] * len(row)
        i_energie = row.index.get_loc("Énergie")
        styles[i_energie] = (f"background-color:{couleur};"
                             f"color:{_text_color(couleur)};font-weight:600")
        i_centrale = row.index.get_loc("Centrale")
        styles[i_centrale] = f"border-left:6px solid {couleur};font-weight:600"
        i_modulation = row.index.get_loc("Modulation")
        if pd.notna(row["Modulation"]) and row["Modulation"] < 0.8:
            styles[i_modulation] = "background-color:#ff4d4d;color:#ffffff;font-weight:600"
        return styles

    fmt = {"Puissance installée (MW)": "{:.0f}",
           "Pmax plage (MW)": "{:.0f}", "Pmin plage (MW)": "{:.0f}",
           "Modulation": "{:.0%}"}
    for c in jour_cols:
        fmt[c] = "{:.2f}"

    styler = (table.style
              .apply(style_row, axis=1)
              .format(fmt, na_rep="n.d."))

    st.dataframe(styler, use_container_width=True, hide_index=True, height=560)
    st.caption(
        f"Énergie quotidienne en GWh (pas de mesure ≈ {step_h:.0f} h). "
        "Clique sur un en-tête pour trier ; l'icône loupe du tableau permet de "
        "rechercher une centrale. Pmax/Pmin et l'énergie portent sur la plage choisie."
    )

    csv = table.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Télécharger le tableau (CSV)", csv,
                       file_name="tableau_centrales.csv", mime="text/csv")


# ----------------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------------
st.set_page_config(page_title="Production RTE par groupe", page_icon="⚡", layout="wide")
st.title("⚡ Production d'électricité par groupe : données RTE")
st.caption(
    "Source : API Actual Generation de RTE (production nette injectée sur le réseau, en MW). "
    "Données construites en H+1 à partir des télémesures."
)

client_id, client_secret = get_credentials()
if not client_id or not client_secret:
    st.error(
        "Identifiants RTE manquants. Ajoutez `RTE_CLIENT_ID` et `RTE_CLIENT_SECRET` "
        "dans les secrets de l'application (voir README)."
    )
    st.stop()

with st.sidebar:
    st.header("Paramètres")
    today = dt.date.today()
    start = st.date_input("Date de début", today - dt.timedelta(days=2),
                          min_value=dt.date(2014, 12, 15), max_value=today)
    end = st.date_input("Date de fin (exclue)", today,
                        min_value=dt.date(2014, 12, 16), max_value=today)
    go = st.button("Charger les données", type="primary", use_container_width=True)
    st.divider()
    st.radio("Affichage", ["📈 Graphiques", "📋 Tableaux"], key="vue")

# Un clic sur le bouton mémorise la demande (dates + état "chargé").
# Indispensable : sans ça, chaque interaction avec un menu relance le script,
# le bouton repasse à False et l'affichage reviendrait à l'écran d'accueil.
if go:
    if end <= start:
        st.warning("La date de fin doit être postérieure à la date de début.")
        st.stop()
    st.session_state["loaded"] = True
    st.session_state["start"] = start
    st.session_state["end"] = end

if not st.session_state.get("loaded"):
    st.info("Choisissez une période dans le menu de gauche puis cliquez sur « Charger les données ».")
    st.stop()

# On rejoue toujours avec les dates mémorisées lors du dernier chargement.
start = st.session_state["start"]
end = st.session_state["end"]

with st.spinner("Récupération des données RTE…"):
    try:
        # load_data est mis en cache : les reruns suivants sont instantanés.
        df = load_data(client_id, client_secret, start, end)
    except requests.HTTPError as e:
        st.error(f"Erreur d'appel à l'API RTE : {e}")
        st.stop()

if df.empty:
    st.warning("Aucune donnée renvoyée pour cette période.")
    st.stop()

# Vue "Tableaux" : si sélectionnée dans le menu de gauche, on l'affiche et on
# s'arrête ici (le code des graphiques ci-dessous n'est alors pas exécuté).
if "Tableaux" in st.session_state.get("vue", ""):
    render_table(df, client_id, client_secret)
    st.stop()

# --- Callbacks pour les boutons d'ajout rapide ---------------------------
def add_famille(groupes_de_la_famille):
    """Ajoute tous les groupes d'une filière à la sélection (sans doublon)."""
    actuel = set(st.session_state.get("groupes_sel", []))
    actuel.update(groupes_de_la_famille)
    st.session_state["groupes_sel"] = sorted(actuel)


def add_familles_multi(df_ref):
    """Ajoute d'un coup toutes les centrales de chaque filière choisie dans
    le multiselect groupé (sans doublon, et sans toucher au reste de la
    sélection)."""
    actuel = set(st.session_state.get("groupes_sel", []))
    for fam in st.session_state.get("familles_multi_sel", []):
        actuel.update(df_ref[df_ref["famille"] == fam]["groupe"].dropna().unique())
    st.session_state["groupes_sel"] = sorted(actuel)


def clear_selection():
    st.session_state["groupes_sel"] = []


# Liste de tous les groupes disponibles dans les données chargées
groupes_dispo = sorted(df["groupe"].dropna().unique())

# Initialisation : au tout premier chargement, on pré-sélectionne les
# 5 groupes les plus producteurs pour que le graphique ne soit pas vide.
if "sel_init" not in st.session_state:
    st.session_state["groupes_sel"] = (
        df.groupby("groupe")["valeur_mw"].mean()
        .sort_values(ascending=False).head(5).index.tolist()
    )
    st.session_state["sel_init"] = True

# Nettoyage : on retire d'éventuels groupes absents de la période courante
# (sinon le multiselect lèverait une erreur).
st.session_state["groupes_sel"] = [
    g for g in st.session_state.get("groupes_sel", []) if g in groupes_dispo
]

# --- 1) Boutons d'ajout rapide : une filière = toutes ses centrales -------
st.subheader("Ajout rapide par filière")
st.caption("Chaque bouton ajoute d'un coup toutes les centrales de la filière au menu ci-dessous.")

# Familles réellement présentes dans les données, dans l'ordre éCO2mix
familles_presentes = [f for f in FAMILLE_ORDER if (df["famille"] == f).any()]

# Affichage en rangées de 5 boutons maximum
for i in range(0, len(familles_presentes), 5):
    rangee = familles_presentes[i:i + 5]
    cols = st.columns(len(rangee))
    for col, fam in zip(cols, rangee):
        with col:
            couleur = ECO2MIX_COLORS[fam]
            n = df[df["famille"] == fam]["groupe"].nunique()
            # pastille de couleur éCO2mix au-dessus du bouton
            st.markdown(
                f"<div style='text-align:center;margin-bottom:2px'>"
                f"<span style='display:inline-block;width:14px;height:14px;"
                f"border-radius:3px;background:{couleur};vertical-align:middle'></span></div>",
                unsafe_allow_html=True,
            )
            groupes_fam = sorted(df[df["famille"] == fam]["groupe"].dropna().unique())
            st.button(
                f"+ {fam} ({n})",
                key=f"btn_{fam}",
                on_click=add_famille,
                args=(groupes_fam,),
                use_container_width=True,
            )

st.button("🗑️ Tout effacer", on_click=clear_selection)

# --- 1bis) Deuxième fenêtre de filtre : sélection groupée de plusieurs
# filières en une seule action (complète les boutons ci-dessus, qui n'en
# ajoutent qu'une à la fois). --------------------------------------------
with st.expander("Sélection groupée par filière (plusieurs à la fois)"):
    st.caption(
        "Choisis une ou plusieurs filières puis clique sur \"Ajouter\" : toutes "
        "leurs centrales sont ajoutées d'un coup au menu ci-dessous."
    )
    col_multi, col_btn = st.columns([4, 1])
    with col_multi:
        st.multiselect(
            "Filières à ajouter",
            options=familles_presentes,
            key="familles_multi_sel",
            label_visibility="collapsed",
        )
    with col_btn:
        st.button(
            "➕ Ajouter", key="btn_ajouter_familles_multi",
            on_click=add_familles_multi, args=(df,), use_container_width=True,
        )

# --- 2) Menu déroulant : sélection manuelle des groupes -------------------
st.multiselect(
    "Groupes de production affichés",
    options=groupes_dispo,
    key="groupes_sel",
    help="Tape pour rechercher un groupe par son nom, ou utilise les boutons ci-dessus.",
)
groupes_sel = st.session_state["groupes_sel"]

if not groupes_sel:
    st.info("Sélectionne au moins un groupe, ou utilise un bouton de filière ci-dessus.")
    st.stop()

dff = df[df["groupe"].isin(groupes_sel)]

# Couleurs : une nuance par centrale, dérivée de la teinte éCO2mix de sa filière.
color_map = build_color_map(dff)

# --- Filtres d'affichage : puissance minimale et variation minimale --------
st.markdown("##### Filtres d'affichage")
col_f1, col_f2 = st.columns(2)
with col_f1:
    filtre_seuil_actif = st.checkbox(
        "Filtrer par puissance minimale", value=False, key="filtre_seuil_actif",
    )
    seuil = st.number_input(
        "N'afficher que les centrales dépassant cette production (MW) sur la plage",
        min_value=0, value=100, step=50, disabled=not filtre_seuil_actif,
        help="Production maximale sur la période. Ex. 100 = on masque les centrales "
             "restées sous 100 MW (à l'arrêt) sur toute la plage.",
    )
with col_f2:
    filtre_variation_actif = st.checkbox(
        "Filtrer par variation minimale", value=False, key="filtre_variation_actif",
    )
    variation_pct = st.number_input(
        "N'afficher que les centrales variant de plus de ce % sur la plage",
        min_value=0.0, value=10.0, step=1.0, disabled=not filtre_variation_actif,
        help="Variation = (max - min) / max sur la plage affichée, en %. Ex. 10 = on "
             "masque les réacteurs restés quasi constants (moins de 10% d'écart entre "
             "leur minimum et leur maximum de production).",
    )

if filtre_seuil_actif:
    maxima = dff.groupby("groupe")["valeur_mw"].max()
    groupes_ok = maxima[maxima > seuil].index
    dff = dff[dff["groupe"].isin(groupes_ok)]

if filtre_variation_actif:
    stats = dff.groupby("groupe")["valeur_mw"].agg(mini="min", maxi="max")
    variation = pd.Series(0.0, index=stats.index)
    non_nul = stats["maxi"] != 0
    variation[non_nul] = (stats["maxi"] - stats["mini"])[non_nul] / stats["maxi"][non_nul] * 100
    groupes_ok = variation[variation > variation_pct].index
    dff = dff[dff["groupe"].isin(groupes_ok)]

if dff.empty:
    st.warning("Aucune centrale ne passe ces filtres sur la plage. Assouplis les critères.")
    st.stop()

# --- Indicateurs ---
col1, col2, col3 = st.columns(3)
col1.metric("Centrales affichées", f"{dff['groupe'].nunique()}")
col2.metric("Points de mesure", f"{len(dff):,}".replace(",", " "))
col3.metric("Production moyenne", f"{dff['valeur_mw'].mean():.0f} MW")

# --- Récap du parc nucléaire par cours d'eau -------------------------------
render_parc_nucleaire()

st.divider()

# --- Fonds des sections de graphiques --------------------------------------
# Chaque section est un grand rectangle pleine largeur : fond de page (noir en
# thème sombre) pour la période sélectionnée, gris pour le mois glissant, fond
# de page pour la semaine glissante, afin de les distinguer d'un coup d'œil.
# Le gris est semi-transparent : foncé sur fond noir, clair sur fond blanc.
# L'ombre sans flou (box-shadow) prolonge le gris jusqu'aux bords de la zone
# principale, et clip-path l'empêche de déborder en haut et en bas.
st.html("""
<style>
.st-key-section_periode, .st-key-section_mois, .st-key-section_semaine {
    padding: 1.5rem 0;
}
.st-key-section_mois {
    background: rgba(128, 128, 128, 0.35);
    box-shadow: 0 0 0 100vmax rgba(128, 128, 128, 0.35);
    clip-path: inset(0 -100vmax);
}
</style>
""")

# --- Graphique 1 : plage choisie dans le menu de gauche -------------------
with st.container(key="section_periode"):
    st.subheader("Production sur la période sélectionnée")
    chart_df = dff.groupby(["debut", "groupe", "famille"])["valeur_mw"].sum().reset_index()
    st.altair_chart(
        line_chart_by_group(chart_df, color_map, x_title="Heure"),
        use_container_width=True,
    )
    graphe_cumule(chart_df, color_map, x_title="Heure")

    # Tableau détaillé
    with st.expander("Voir le détail par centrale (tableau + export CSV)"):
        st.dataframe(
            dff.sort_values("debut").rename(columns={
                "code_eic": "Code EIC", "groupe": "Centrale", "famille": "Filière",
                "debut": "Début", "valeur_mw": "Valeur (MW)",
            })[["Code EIC", "Centrale", "Filière", "Début", "Valeur (MW)"]],
            use_container_width=True, hide_index=True,
        )
        csv = dff.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Télécharger en CSV", csv,
                           file_name=f"production_rte_{start}_{end}.csv", mime="text/csv")


# ==========================================================================
# Graphiques 2 et 3 : fenêtres glissantes jusqu'à aujourd'hui
# (mêmes centrales sélectionnées ; données chargées séparément)
# ==========================================================================
def graphe_glissant(titre, jours_options, defaut, rule, label_x, key, x_format="%d/%m %Hh"):
    """Affiche un graphique sur une fenêtre glissante avec menu de durée."""
    st.subheader(titre)
    jours = st.selectbox(
        "Durée affichée (jusqu'à aujourd'hui)",
        options=jours_options,
        index=jours_options.index(defaut),
        format_func=lambda j: f"{j} derniers jours",
        key=key,
    )
    with st.spinner(f"Chargement des {jours} derniers jours…"):
        try:
            d = fenetre_glissante(client_id, client_secret, jours)
        except requests.HTTPError as e:
            st.error(f"Erreur d'appel à l'API RTE : {e}")
            return
    d = d[d["groupe"].isin(groupes_sel)]
    if d.empty:
        st.info("Aucune donnée pour les centrales sélectionnées sur cette fenêtre.")
        return
    # Ré-échantillonnage pour garder le graphique lisible et rapide
    d = (
        d.set_index("debut").groupby(["groupe", "famille"])["valeur_mw"]
        .resample(rule).mean().reset_index()
    )
    st.altair_chart(
        line_chart_by_group(d, color_map, x_title=label_x, height=360, x_format=x_format),
        use_container_width=True,
    )
    graphe_cumule(d, color_map, x_title=label_x, height=360, x_format=x_format)

# Graphique 2 : mois glissant (pas journalier), sur fond gris
with st.container(key="section_mois"):
    graphe_glissant(
        "Mois glissant", jours_options=[15, 30, 60, 90], defaut=30,
        rule="1D", label_x="Jour", key="select_mois", x_format="%d/%m",
    )
    st.caption("Moyenne journalière par centrale.")

# Graphique 3 : semaine glissante (pas horaire)
with st.container(key="section_semaine"):
    graphe_glissant(
        "Semaine glissante", jours_options=[3, 7, 14, 21], defaut=7,
        rule="1h", label_x="Heure", key="select_semaine",
    )
    st.caption("Moyenne horaire par centrale.")
