"""
elabora_presenze.py
Elabora le timbrature aziendali e popola la matrice ore presenza.
Business logic:
  - Lun-Ven ordinario: 08:00-12:00 + 14:00-18:00 = max 8h
  - Sabato: 100% straordinario
  - Straordinari Lun-Ven: franchigia 15 min, scatti di 15 min arrotondati per difetto
  - Giustificativi (FE, M1, DS, CO, ecc.) = 8h ordinarie se nessuna timbratura
"""

import csv
import re
from datetime import datetime, timedelta, time
from collections import defaultdict

# ─── PERCORSI FILE ────────────────────────────────────────────────────────────
SORGENTE   = r"C:\Users\pc2\Desktop\claude code\tutti marzo 2026.csv"
MATRICE_IN = r"C:\Users\pc2\Desktop\claude code\matrice FILE ORE PRESENZA marzo 2026.csv"
OUTPUT_CSV = r"C:\Users\pc2\Desktop\claude code\ore_elaborate_marzo2026.csv"

# ─── COSTANTI ─────────────────────────────────────────────────────────────────
ORE_STANDARD     = 8.0
MATTINO_START    = time(8, 0)
MATTINO_END      = time(12, 0)
POMERIGGIO_START = time(14, 0)
POMERIGGIO_END   = time(18, 0)
FRANCHIGIA_MIN   = 15          # minuti di tolleranza prima degli straordinari
SCATTO_MIN       = 15          # arrotondamento per difetto

# Codici giustificativo → le ore sono considerate come ore ordinarie (8h di default)
GIUSTIFICATIVI = {
    'FE', 'M1', 'M8', 'M9', 'DS', 'CO', 'CO 8', 'FS', 'I1', 'AL',
    'A4', 'PE', 'ST', 'RO', 'T1', 'TE', 'P1', 'P2', 'P9', 'L109',
    'CIG', 'CM', 'infortunio', 'malattia', 'ferie',
}

# ─── UTILITÀ ──────────────────────────────────────────────────────────────────

def arrotonda_scatti(minuti_totali: float, scatto: int = SCATTO_MIN) -> float:
    """Arrotonda per difetto allo scatto di 15 min; restituisce ore decimali."""
    scatti = int(minuti_totali // scatto)
    return round(scatti * scatto / 60, 4)


def calcola_ordinarie_straordinarie(eventi: list, giorno_settimana: int):
    """
    Riceve lista di (datetime, tipo) per un giorno, restituisce (ore_ord, ore_str).
    giorno_settimana: 0=lun … 6=dom
    """
    eventi = sorted(eventi, key=lambda x: x[0])

    # Costruisci intervalli lavorati
    intervalli = []
    entrata = None
    for orario, tipo in eventi:
        t = tipo.strip().lower()
        if t in ('entrata', 'ritorno'):
            entrata = orario
        elif t in ('uscita', 'pausa') and entrata:
            intervalli.append((entrata, orario))
            entrata = None

    if not intervalli:
        return 0.0, 0.0

    # Sabato → tutto straordinario
    if giorno_settimana == 5:
        tot_min = sum((f - i).total_seconds() / 60 for i, f in intervalli)
        return 0.0, round(tot_min / 60, 2)

    # Lun-Ven: calcola parte ordinaria e straordinaria
    std_start_am = MATTINO_START
    std_end_am   = MATTINO_END
    std_start_pm = POMERIGGIO_START
    std_end_pm   = POMERIGGIO_END

    def overlap_minutes(a_start, a_end, b_start, b_end):
        """Minuti di overlap tra due intervalli di time."""
        a_s = datetime.combine(datetime.today(), a_start)
        a_e = datetime.combine(datetime.today(), a_end)
        b_s = datetime.combine(datetime.today(), b_start)
        b_e = datetime.combine(datetime.today(), b_end)
        delta = min(a_e, b_e) - max(a_s, b_s)
        return max(0, delta.total_seconds() / 60)

    ord_min = 0.0
    tot_min = 0.0

    for inizio, fine in intervalli:
        dur = (fine - inizio).total_seconds() / 60
        tot_min += dur
        t_i = inizio.time()
        t_f = fine.time()
        ord_min += overlap_minutes(t_i, t_f, std_start_am, std_end_am)
        ord_min += overlap_minutes(t_i, t_f, std_start_pm, std_end_pm)

    ore_ord = min(round(ord_min / 60, 4), ORE_STANDARD)

    # Straordinari = tempo oltre l'orario standard, con franchigia+scatti
    extra_min = max(0, tot_min - ORE_STANDARD * 60)
    if extra_min < FRANCHIGIA_MIN:
        ore_str = 0.0
    else:
        ore_str = arrotonda_scatti(extra_min)

    return round(ore_ord, 2), round(ore_str, 2)


# ─── 1. LEGGI TIMBRATURE ──────────────────────────────────────────────────────
print("=" * 60)
print("ELABORAZIONE PRESENZE MARZO 2026")
print("=" * 60)

timbrature = defaultdict(list)   # {(nome_normalizzato, date): [(datetime, tipo)]}
anomalie   = []

for enc in ('utf-8-sig', 'latin-1'):
    try:
        with open(SORGENTE, encoding=enc) as f:
            reader = csv.DictReader(f, delimiter=';')
            for row_num, row in enumerate(reader, start=2):
                nome = row.get('Nome utente', '').strip()
                orario_str = row.get('Orario di Timbratura', '').strip()
                tipo = row.get('Entrata / Uscita', '').strip()
                codice = row.get('Codice lavoro', '').strip()
                if not nome or not orario_str:
                    continue
                try:
                    orario = datetime.strptime(orario_str, '%d/%m/%Y %H:%M:%S')
                except ValueError:
                    anomalie.append(f"  Riga {row_num}: formato data non riconosciuto → '{orario_str}'")
                    continue
                key = (nome.lower(), orario.date())
                timbrature[key].append((orario, tipo, codice))
        print(f"[OK] File sorgente letto (encoding: {enc})")
        break
    except UnicodeDecodeError:
        continue

# ─── 2. LEGGI DIPENDENTI DAL TEMPLATE ─────────────────────────────────────────
# Riga 4 (indice 3) = intestazione giorni; righe employee = quelle con nome in col 0
dipendenti_template = []

for enc in ('utf-8-sig', 'latin-1'):
    try:
        with open(MATRICE_IN, encoding=enc) as f:
            rows = list(csv.reader(f, delimiter=';'))
        # Riga header (index 3): col 0 = "COGNOME E NOME", col 2..32 = giorni 1..31
        header_row = rows[3]  # es. ['COGNOME E NOME', '', '1', '2', ...]
        # Trova le colonne dei giorni
        day_cols = {}
        for ci, val in enumerate(header_row):
            v = val.strip()
            if v.isdigit() and 1 <= int(v) <= 31:
                day_cols[int(v)] = ci

        # Individua i blocchi dipendente (riga con nome non vuoto in col 0,
        # seguita da righe "lavorate", "straordinarie", ecc.)
        SKIP_CELLS = {
            'mese', 'cognome e nome', 'mettere solo le ore in fe/pe', '',
            'stipendio deve arrivare a euro', 'premio 350,00', 'premio 100,00',
            '2.000,00',
        }

        i = 4
        while i < len(rows):
            nome_cell = rows[i][0].strip() if rows[i] else ''
            if nome_cell and nome_cell.lower() not in SKIP_CELLS \
                    and not nome_cell.startswith(';'):
                dipendenti_template.append({
                    'nome': nome_cell,
                    'row_lav': i,
                    'row_str': i + 1 if (i+1) < len(rows) else None,
                    'row_fep': None,
                })
                # Cerca riga Fe/Pe nelle successive 3 righe
                for offset in range(1, 5):
                    if i + offset >= len(rows):
                        break
                    cell = rows[i + offset][1].strip() if len(rows[i+offset]) > 1 else ''
                    if cell.upper() in ('FE/PE', 'FE/PE ', 'FE / PE'):
                        dipendenti_template[-1]['row_fep'] = i + offset
                        break
            i += 1

        print(f"[OK] Template letto (encoding: {enc}) – {len(dipendenti_template)} dipendenti trovati")
        break
    except UnicodeDecodeError:
        continue

# ─── 3. NORMALIZZA NOME per il match ─────────────────────────────────────────
def normalizza(s):
    """Lowercase, rimuovi spazi doppi, inverti 'Cognome Nome' ↔ 'Nome Cognome'."""
    return re.sub(r'\s+', ' ', s.strip().lower())

# Costruisci mappa nome_template → nome_sorgente
def build_name_map():
    sorgente_nomi = set(nome for (nome, _) in timbrature.keys())
    mappa = {}
    for d in dipendenti_template:
        nt = normalizza(d['nome'])
        parti_t = set(nt.split())

        best = None
        best_score = 0
        for sn in sorgente_nomi:
            sn_norm = normalizza(sn)
            # match esatto
            if sn_norm == nt:
                best = sn
                best_score = 999
                break
            # match parziale: quante parole del sorgente compaiono nel template
            parti_s = set(sn_norm.split())
            score = len(parti_s & parti_t)
            if score > best_score:
                best_score = score
                best = sn

        if best and best_score > 0:
            mappa[nt] = best

    return mappa

name_map = build_name_map()
print(f"[OK] Match nome: {len(name_map)}/{len(dipendenti_template)} dipendenti abbinati")

non_abbinati = [d['nome'] for d in dipendenti_template
                if normalizza(d['nome']) not in name_map]
if non_abbinati:
    print(f"  [WARN] Non abbinati nel sorgente: {non_abbinati}")

# ─── 4. CALCOLA ORE PER OGNI DIPENDENTE/GIORNO ───────────────────────────────
from calendar import monthrange

anno, mese = 2026, 3
_, num_giorni = monthrange(anno, mese)

# risultati[nome_template][giorno] = {'ord': x, 'str': y, 'note': z}
risultati = {}

for d in dipendenti_template:
    nt = normalizza(d['nome'])
    sn = name_map.get(nt)
    risultati[d['nome']] = {}

    for g in range(1, num_giorni + 1):
        data = datetime(anno, mese, g).date()
        dow  = data.weekday()   # 0=lun, 6=dom

        # domenica → salta
        if dow == 6:
            continue

        key = (sn, data) if sn else None
        eventi = timbrature.get(key, []) if key else []

        # Controlla giustificativi nel template (riga Fe/Pe)
        giustificativo = ''
        if d['row_fep'] is not None and d['row_fep'] < len(rows):
            fep_row = rows[d['row_fep']]
            col = day_cols.get(g)
            if col and col < len(fep_row):
                val = fep_row[col].strip()
                if val and val.upper() in {j.upper() for j in GIUSTIFICATIVI}:
                    giustificativo = val.upper()

        if eventi:
            ore_ord, ore_str = calcola_ordinarie_straordinarie(
                [(o, t) for o, t, _ in eventi], dow
            )
        elif giustificativo:
            ore_ord, ore_str = ORE_STANDARD, 0.0
        else:
            ore_ord, ore_str = 0.0, 0.0

        risultati[d['nome']][g] = {
            'ord':  ore_ord,
            'str':  ore_str,
            'note': giustificativo,
            'dow':  dow,
        }

# ─── 5. SCRIVI OUTPUT CSV ─────────────────────────────────────────────────────
GIORNI = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']
fieldnames = [
    'Dipendente', 'Data', 'Giorno',
    'Ore Ordinarie', 'Straordinari (h)', 'Giustificativo', 'Note'
]

righe_output = []
for d in dipendenti_template:
    for g in range(1, num_giorni + 1):
        if g not in risultati[d['nome']]:
            continue
        r = risultati[d['nome']][g]
        data = datetime(anno, mese, g)
        righe_output.append({
            'Dipendente':       d['nome'],
            'Data':             data.strftime('%d/%m/%Y'),
            'Giorno':           GIORNI[r['dow']],
            'Ore Ordinarie':    str(r['ord']).replace('.', ','),
            'Straordinari (h)': str(r['str']).replace('.', ','),
            'Giustificativo':   r['note'],
            'Note':             'SAB→100% STR' if r['dow'] == 5 and r['str'] > 0 else '',
        })

with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
    writer.writeheader()
    writer.writerows(righe_output)

# ─── 6. RIEPILOGO ─────────────────────────────────────────────────────────────
print()
print("-" * 60)
print("RIEPILOGO MENSILE PER DIPENDENTE")
print("-" * 60)
print(f"{'Dipendente':<22} {'Ord.':>6} {'Str.':>6}  {'Giorni':>6}")
print("-" * 60)

for d in dipendenti_template:
    tot_ord = sum(v['ord'] for v in risultati[d['nome']].values())
    tot_str = sum(v['str'] for v in risultati[d['nome']].values())
    giorni  = sum(1 for v in risultati[d['nome']].values() if v['ord'] > 0 or v['str'] > 0)
    print(f"{d['nome']:<22} {tot_ord:>6.2f} {tot_str:>6.2f}  {giorni:>6}")

print("-" * 60)
print(f"\n[OK] Righe scritte: {len(righe_output)}")
print(f"[OK] Output: {OUTPUT_CSV}")

if anomalie:
    print(f"\n[!] ANOMALIE ({len(anomalie)}):")
    for a in anomalie:
        print(a)

print("\nElaborazione completata.")
