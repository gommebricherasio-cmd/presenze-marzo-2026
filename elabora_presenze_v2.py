"""
elabora_presenze_v2.py
Legge le timbrature da 'tutti marzo 2026.csv',
calcola ore lavorate e straordinari,
e produce un file di output con il LAYOUT della matrice presenze.

Business logic:
  - Lun-Ven: orario ordinario 08:00-12:00 + 14:00-18:00 = max 8h
  - Sabato: 100% straordinario
  - Straordinari Lun-Ven: franchigia 15 min, scatti 15 min arrotondati per difetto
  - Giustificativi dalla riga Fe/Pe del template = 8h ordinarie se nessuna timbratura
  - Ore espresse in centesimi (es. 8,50 = 8h30min)
"""

import csv, re, sys
from datetime import datetime, timedelta, time
from collections import defaultdict
from calendar import monthrange

# ── ENCODING CONSOLE ────────────────────────────────────────────────────────
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ── FILE ────────────────────────────────────────────────────────────────────
SORGENTE    = r"C:\Users\pc2\Desktop\claude code\tutti marzo 2026.csv"
TEMPLATE    = r"C:\Users\pc2\Desktop\claude code\matrice FILE ORE PRESENZA marzo 2026.csv"
OUTPUT      = r"C:\Users\pc2\Desktop\claude code\matrice_ELABORATA_marzo2026.csv"

ANNO, MESE  = 2026, 3
_, NUM_GG   = monthrange(ANNO, MESE)

# ── COSTANTI ────────────────────────────────────────────────────────────────
ORE_STD          = 8.0
FRANCHIGIA_MIN   = 15
SCATTO_MIN       = 15
GIUSTIFICATIVI   = {
    'FE','M1','M8','M9','DS','CO','CO 8','FS','I1','AL',
    'A4','PE','ST','RO','T1','TE','P1','P2','P9','L109',
    'CIG','CM','INFORTUNIO','MALATTIA','FERIE',
}

# ── UTILITÀ ─────────────────────────────────────────────────────────────────

def min_to_centesimi(minuti: float) -> float:
    """Converte minuti in ore centesimali (es. 90 min → 1.50)."""
    ore  = int(minuti // 60)
    mins = minuti % 60
    return round(ore + mins / 60, 2)

def arrotonda_scatti(minuti: float, scatto: int = SCATTO_MIN) -> float:
    """Arrotonda per difetto allo scatto di 15 min, restituisce minuti."""
    return (int(minuti) // scatto) * scatto

def time_overlap_min(s1: time, e1: time, s2: time, e2: time) -> float:
    """Minuti di sovrapposizione tra [s1,e1) e [s2,e2)."""
    base = datetime(2000, 1, 1)
    a = max(datetime.combine(base, s1), datetime.combine(base, s2))
    b = min(datetime.combine(base, e1), datetime.combine(base, e2))
    return max(0.0, (b - a).total_seconds() / 60)

def calcola_giorno(eventi: list, dow: int):
    """
    eventi: [(datetime, tipo), ...]
    dow:    0=lun … 6=dom
    Restituisce (ore_ord_centesimi, ore_str_centesimi).
    """
    eventi = sorted(eventi, key=lambda x: x[0])

    # Costruisci intervalli effettivi
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

    tot_min = sum((f - i).total_seconds() / 60 for i, f in intervalli)

    # ── SABATO ──────────────────────────────────────────────────────────────
    if dow == 5:
        return 0.0, round(min_to_centesimi(tot_min), 2)

    # ── LUN-VEN ─────────────────────────────────────────────────────────────
    AM_S, AM_E = time(8, 0),  time(12, 0)
    PM_S, PM_E = time(14, 0), time(18, 0)

    ord_min = 0.0
    for inizio, fine in intervalli:
        ti, tf = inizio.time(), fine.time()
        ord_min += time_overlap_min(ti, tf, AM_S, AM_E)
        ord_min += time_overlap_min(ti, tf, PM_S, PM_E)

    ore_ord = min_to_centesimi(min(ord_min, ORE_STD * 60))

    extra_min = max(0.0, tot_min - ORE_STD * 60)
    if extra_min < FRANCHIGIA_MIN:
        ore_str = 0.0
    else:
        ore_str = min_to_centesimi(arrotonda_scatti(extra_min))

    return round(ore_ord, 2), round(ore_str, 2)


# ════════════════════════════════════════════════════════════════════════════
# 1. LEGGI TIMBRATURE
# ════════════════════════════════════════════════════════════════════════════
print("=" * 60)
print("ELABORAZIONE PRESENZE MARZO 2026")
print("=" * 60)

timbrature = defaultdict(list)   # (nome_lower, date) -> [(dt, tipo)]
anomalie   = []

for enc in ('utf-8-sig', 'latin-1'):
    try:
        with open(SORGENTE, encoding=enc) as f:
            reader = csv.DictReader(f, delimiter=';')
            for rn, row in enumerate(reader, 2):
                nome = row.get('Nome utente', '').strip()
                ts   = row.get('Orario di Timbratura', '').strip()
                tipo = row.get('Entrata / Uscita', '').strip()
                if not nome or not ts:
                    continue
                try:
                    dt = datetime.strptime(ts, '%d/%m/%Y %H:%M:%S')
                except ValueError:
                    anomalie.append(f"  Riga {rn}: data non valida '{ts}'")
                    continue
                timbrature[(nome.lower(), dt.date())].append((dt, tipo))
        print(f"[OK] Sorgente letto (enc={enc}): "
              f"{sum(len(v) for v in timbrature.values())} timbrature, "
              f"{len({k[0] for k in timbrature})} dipendenti")
        break
    except UnicodeDecodeError:
        continue

# ════════════════════════════════════════════════════════════════════════════
# 2. LEGGI TEMPLATE e individua struttura
# ════════════════════════════════════════════════════════════════════════════
for enc in ('utf-8-sig', 'latin-1', 'cp1252'):
    try:
        with open(TEMPLATE, encoding=enc) as f:
            template_rows = [r for r in csv.reader(f, delimiter=';')]
        print(f"[OK] Template letto (enc={enc}): {len(template_rows)} righe")
        break
    except UnicodeDecodeError:
        continue

# Riga 4 (indice 3) = intestazione giorni
header_row = template_rows[3]
day_col = {}   # giorno (int 1-31) -> indice colonna
for ci, val in enumerate(header_row):
    v = val.strip()
    if v.isdigit() and 1 <= int(v) <= 31:
        day_col[int(v)] = ci

print(f"[OK] Colonne giorni trovate: {len(day_col)} (da col {min(day_col.values())} a {max(day_col.values())})")

# Individua blocchi dipendente (ogni blocco: nome, lavorate, straordinarie, [legge104], Fe/Pe)
SKIP = {
    'mese','cognome e nome','mettere solo le ore in fe/pe','',
    'stipendio deve arrivare a euro','2.000,00',
}
SUB_LABELS = {'lavorate','straordinarie','legge 104','fe/pe','fe/pe ','fe / pe',
              'lavorate ','straordinarie ', 'legge 104 '}
PREMIO_RE  = re.compile(r'^premio\s+[\d.,]+', re.IGNORECASE)

dipendenti = []   # lista di dict con info blocco
i = 4
while i < len(template_rows):
    row = template_rows[i]
    col0 = row[0].strip() if row else ''
    col1 = (row[1].strip().lower() if len(row) > 1 else '')
    # La riga principale del dipendente ha col0=nome e col1='lavorate'
    if (col0
            and col0.lower() not in SKIP
            and col1.startswith('lavorate')
            and not PREMIO_RE.match(col0)):
        # Questo è un blocco dipendente
        blocco = {
            'nome':    col0,
            'row_lav': i,
            'row_str': None,
            'row_l04': None,
            'row_fep': None,
        }
        # Cerca le sotto-righe nelle prossime 4 righe
        for off in range(1, 6):
            if i + off >= len(template_rows):
                break
            sr = template_rows[i + off]
            c0 = sr[0].strip() if sr else ''
            c1 = (sr[1].strip().lower() if len(sr) > 1 else '')
            # Se col0 non vuota e non è un sub-label, siamo al prossimo dip.
            if c0 and c0.lower() not in SKIP and c1 not in SUB_LABELS and not PREMIO_RE.match(c0):
                break
            if 'straordinari' in c1:
                blocco['row_str'] = i + off
            elif 'legge 104' in c1 or 'legge104' in c1:
                blocco['row_l04'] = i + off
            elif 'fe/pe' in c1 or 'fe / pe' in c1:
                blocco['row_fep'] = i + off
        dipendenti.append(blocco)
    i += 1

print(f"[OK] Dipendenti nel template: {[d['nome'] for d in dipendenti]}")

# ════════════════════════════════════════════════════════════════════════════
# 3. MATCH NOMI template <-> sorgente
# ════════════════════════════════════════════════════════════════════════════
sorgente_nomi = list({k[0] for k in timbrature})   # già in lowercase

def best_match(nome_tmpl: str) -> str | None:
    """Trova il nome sorgente che meglio corrisponde al nome template."""
    nt = re.sub(r'\s+', ' ', nome_tmpl.strip().lower())
    parti_t = set(nt.split())
    best, best_score = None, 0
    for sn in sorgente_nomi:
        if sn == nt:
            return sn   # match esatto
        score = len(set(sn.split()) & parti_t)
        if score > best_score:
            best_score, best = score, sn
    return best if best_score > 0 else None

for d in dipendenti:
    d['sorgente'] = best_match(d['nome'])

abbinati = [d for d in dipendenti if d['sorgente']]
print(f"[OK] Match: {len(abbinati)}/{len(dipendenti)} dipendenti abbinati")
non_abb = [d['nome'] for d in dipendenti if not d['sorgente']]
if non_abb:
    print(f"  [WARN] Senza timbrature: {non_abb}")

# ════════════════════════════════════════════════════════════════════════════
# 4. CALCOLA ORE e POPOLA TEMPLATE
# ════════════════════════════════════════════════════════════════════════════
def fmt(val: float) -> str:
    """Formatta ore centesimali: 0 -> '', altrimenti con virgola."""
    if val == 0.0:
        return ''
    return str(val).replace('.', ',')

# Lavora su una copia delle righe del template
out_rows = [list(r) for r in template_rows]

# Assicura che ogni riga abbia abbastanza colonne
max_col = max(day_col.values()) + 1
for r in out_rows:
    while len(r) < max_col:
        r.append('')

riepilogo = []

for d in dipendenti:
    nome_sorgente = d['sorgente']
    totale_ord, totale_str, giorni_lav = 0.0, 0.0, 0

    for g in range(1, NUM_GG + 1):
        data = datetime(ANNO, MESE, g).date()
        dow  = data.weekday()   # 0=lun … 6=dom
        col  = day_col.get(g)
        if col is None:
            continue

        # Leggi eventuale giustificativo dalla riga Fe/Pe del template
        giustif = ''
        if d['row_fep'] is not None:
            fep_val = template_rows[d['row_fep']][col].strip() if col < len(template_rows[d['row_fep']]) else ''
            if fep_val.upper() in GIUSTIFICATIVI:
                giustif = fep_val.upper()

        key = (nome_sorgente, data) if nome_sorgente else None
        eventi = [(dt, tp) for dt, tp in timbrature.get(key, [])]

        if eventi:
            ore_ord, ore_str = calcola_giorno(eventi, dow)
        elif giustif:
            ore_ord, ore_str = ORE_STD, 0.0
        elif dow == 6:   # domenica
            ore_ord, ore_str = 0.0, 0.0
        else:
            ore_ord, ore_str = 0.0, 0.0

        # Scrivi ore nella riga "lavorate"
        if d['row_lav'] is not None and ore_ord > 0:
            out_rows[d['row_lav']][col] = fmt(ore_ord)

        # Scrivi straordinari nella riga "straordinarie"
        if d['row_str'] is not None and ore_str > 0:
            out_rows[d['row_str']][col] = fmt(ore_str)

        if ore_ord > 0 or ore_str > 0:
            totale_ord += ore_ord
            totale_str += ore_str
            giorni_lav += 1

    riepilogo.append((d['nome'], totale_ord, totale_str, giorni_lav))

# ════════════════════════════════════════════════════════════════════════════
# 5. SCRIVI OUTPUT
# ════════════════════════════════════════════════════════════════════════════
with open(OUTPUT, 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f, delimiter=';')
    writer.writerows(out_rows)

print()
print("-" * 60)
print(f"{'Dipendente':<22} {'Ord.':>7} {'Str.':>7}  {'GG':>4}")
print("-" * 60)
for nome, ord_, str_, gg in riepilogo:
    print(f"{nome:<22} {ord_:>7.2f} {str_:>7.2f}  {gg:>4}")
print("-" * 60)
print(f"\n[OK] Output scritto: {OUTPUT}")

if anomalie:
    print(f"\n[!] Anomalie ({len(anomalie)}):")
    for a in anomalie:
        print(a)

print("\nElaborazione completata.")
