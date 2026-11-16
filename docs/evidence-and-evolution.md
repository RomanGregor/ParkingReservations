# Evidence and evolution

# C01 Engineering Spike

Spike variant: **A – Persistence** (issue #1, PR from `spike/sqlite-persistence`).

Question / unknown:
Can a `Reservation` (with a `State` enum and timezone-aware `datetime`s) be
stored in SQLite and loaded back *identically* in a new connection, so that
the overlap rule can be evaluated against persisted data? SQLite has no
datetime or enum column type, so we were not sure the round trip is lossless.

What we did:
Wrote `src/parking/repository.py` (`ReservationRepository` with `save`,
`get`, `for_place`) and `tests/test_persistence_spike.py`, which saves a
confirmed reservation to a temporary `.db` file, closes the connection,
opens a fresh one, loads by id and compares with the original. A second test
confirms a new reservation against reservations loaded from the DB.

Run: `PYTHONPATH=src python3 -m unittest discover -s tests -v`

Observed result:
```
test_loaded_reservations_feed_the_overlap_rule ... ok
test_reservation_survives_round_trip ... ok
Ran 5 tests in 0.003s
OK
```
`datetime.isoformat()` / `datetime.fromisoformat()` preserves the UTC offset,
`loaded == original` holds (dataclass equality), and `State(row)` restores the
enum. Note: `end` is a reserved word in SQLite, so the column is quoted.

Decision / what changes because of the result:
- Times are stored as ISO-8601 strings in UTC (recorded as D3 in
  `docs/architecture-and-decisions.md`); no custom adapters needed.
- The overlap rule stays in the domain and takes loaded reservations as
  input; the repository does not duplicate the rule.
- SQLite is sufficient for the CP1 walking skeleton. The `Unknown` in the
  Project Frame (real load) stays open.

## Evidence C02: specifikace → běžící aplikace

Specifikace: `docs/specification.md`.

**Přijatá baseline:** Specification Baseline v0.2, schválená týmem.
- v0.1: OP-01..OP-04 a BR-01..BR-05.
- v0.2: změna „schvalovací proces“. Přidává stavy `PENDING_APPROVAL`,
  `REJECTED`, `EXPIRED` a operaci OP-05 Approve Reservation.

**Předvedené základní operace:** Create, Check Availability, Confirm, Cancel
a z v0.2 Approve/Reject. Doména je v `src/parking/domain.py`. Ovládá se
z tkinter GUI (`src/parking/gui.py`, `python run.py`), kde místo `VIP-01`
vyžaduje schválení.

**Skutečně provedené příklady ověření:** 32 automatických testů.
- 27 v `tests/test_domain.py`, rozdělených podle operací
  (`OP01CreateReservation`, `OP02CheckAvailability`,
  `OP03ConfirmReservation`, `OP04CancelReservation`,
  `OP05ApproveReservation` včetně Reject a Expire).
- 2 v `tests/test_persistence_spike.py` (C01 spike).
- 3 v `tests/test_concurrency.py`: REQ-04 (dvě souběžná konfliktní
  potvrzení), souběh Cancel × Confirm a operace nad neexistující rezervací.
  Běží nad skutečným SQLite souborem, dvě vlákna mají každé vlastní
  spojení.

Každá operace má aspoň jeden úspěšný a jeden negativní nebo hraniční
příklad. Vstupy odpovídají tabulkám „Příklady ověření“ ve specifikaci.

```
$ PYTHONPATH=src python3 -m unittest discover -s tests
................................
Ran 32 tests in 0.535s

OK
```

**Nalezený nesoulad a způsob vyřešení:** ve všech případech jsme opravili
implementaci podle specifikace.
- **Cancel bez časové hranice.** `cancel()` z C01 nekontrolovala čas.
  Specifikace (BR-03) stanoví `now < start`, proto přibyl parametr `now`.
  Chyběla tu hranice ve specifikaci z C01, ne záměr.
- **Confirm nerozlišoval místa.** `confirm()` neuměla rozlišit místa se
  schválením, proto dostala parametr `place` (OP-03 v0.2).
- **Create přijímal začátek v minulosti.** `create_reservation()` přijala
  i `start` v minulosti, proto přibylo pravidlo BR-05
  (`start >= now + 1 h`). Výchozí interval v GUI byl pevně „dnes
  08:00–10:00“, což od 7:00 BR-05 porušuje. Nově je výchozí interval
  `now + 2 h` až `now + 4 h`.
- **Rozhodnutí po začátku rezervace.** Při revizi specifikace vyšlo najevo,
  že `confirm()`, `approve()` a `reject()` nekontrolují čas. Šlo tak
  potvrdit rezervaci zpětně nebo schválit žádost, která už měla vypršet
  (REQ-08). Všechny tři teď vyžadují `now < start`. Testy
  `test_cannot_confirm_after_start` a
  `test_cannot_approve_or_reject_after_start` ověřují hranici
  `now == start`.
- **REQ-04 přijatý, ale nesplněný.** Specifikace vedla REQ-04 jako
  přijatý požadavek a zároveň jako TBD, protože aplikace kolizi
  zkontrolovala a zapsala ve dvou krocích. Chyba byla v implementaci, ne
  v požadavku: dvojí rezervace místa je přesně to, čemu má systém
  zabránit. Všechny změny stavu teď jdou přes
  `ReservationRepository.apply()`, které čtení, kontrolu i zápis provede
  pod jedním zámkem pro zápis. Test v `tests/test_concurrency.py` bez
  zámku prokazatelně selže (obě rezervace `CONFIRMED`, resp. zrušená
  rezervace přepsaná na `CONFIRMED`) a se zámkem projde.
- **Neplatný interval v Check Availability.** `is_available()` pro
  `end <= start` odpovídala „available“. Nově dotaz zamítne
  (`test_invalid_interval_is_rejected`).

**Shrnutí dopadu změny:** viz změnová karta a „Dopad změny C02“ ve
specifikaci.
- Dotčené: REQ-03 (Confirm se větví podle místa), REQ-05 a BR-03 (zrušit
  jde i `PENDING_APPROVAL`).
- Nedotčené: REQ-01, REQ-02, REQ-04, BR-01, BR-02, BR-04, BR-05.
- Nová operace OP-05 Approve/Reject pro existujícího aktéra Facility
  manager.

**Zbývající předpoklad / neznámá:**
- Přihlášení Drivera, role Facility managera a seznam míst řeší zatím GUI
  (pevný seznam míst). Doména role nerozlišuje. Patří to do architektury
  v C03.
- Lhůta pro schválení končí na `start` rezervace. Jestli má být kratší,
  zadání neurčuje (TBD u OP-05).

**Architektonické drivery přenesené do C03:**
- **AD-01:** atomické potvrzení a schválení (REQ-04, REQ-06). Aplikace je
  teď splňuje zámkem celé SQLite databáze. Pro C03 zůstává, kde má ležet
  hranice transakce a jestli zámek celé databáze vydrží víc klientů.
- **AD-02:** schvalování a vypršení v čase. Vypršení se teď kontroluje jen
  při obnovení GUI. Chybí časovač a napojení na Notification Service z C01.

**Commit / tag aplikace:** větev `c02-specification` (commity jsou v historii
`main`), aplikace odpovídající
baseline v0.2 je commit `3aa3a47` (doména `569e723`). Navazuje na `7f21516`
z C01.

## C03 — Architecture Evidence

Podrobnosti ke všem bodům jsou v `docs/architecture-and-decisions.md`,
sekce „C03 — Architektura“.

**Baseline:** v0.2

**Part A:** AS-IS nad commitem `bb72a77`. Use-casy skládá GUI (F1),
atomičnost drží `repository.apply` jen při správném použití (F2), katalog
míst je v GUI (F3), vypršení jen při obnovení (F4), notifikace chybí (F5),
role se nerozlišují (F6).

**Drivers:** AD-01 atomické Confirm/Approve (BR-02, REQ-04, REQ-06), AD-02
odložené schválení a vypršení (REQ-06..08), AD-03 selhání Notification
Service, AD-04 druhý klient v CP1 (HTTP).

**Decision question:** Kde má být vlastněno provádění přechodů stavu
`Reservation` včetně hranice transakce, aby BR-02 a statechart v0.2 platily
stejně pro každého klienta i při odloženém schválení?

**Alternatives:** A — klient skládá use-case a volá `repository.apply`;
B — aplikační služba `ReservationService` je jediný vstup pro přechody.
(C — trigger v DB — zavržena v E1.)

**Scenario walkthrough:** Confirm na `VIP-01` → `PENDING_APPROVAL` → konec
požadavku → souběžné Approve dvou překrývajících se žádostí ve dvou
instancích, uplynutí `start`, výpadek notifikace. Obě varianty fungují,
A jen pokud každý klient použije `apply` správně.

**ADR:** ADR-04 — rozhodnuto B. Negativní důsledky: další vrstva, riziko
přerostlé služby, zámek celé DB, vypršení jen s běžící instancí, notifikace
bez opakování.

**Views:**
- domain class — C1
- context — G1
- static architecture — G2 (5 prvků, přidělení R1–R8)
- state ownership — G3 (9 přechodů, vše Reservation Management)
- runtime/deployment — G4 (1 deployable, N instancí, 1 SQLite soubor)
- design sequence — H1 (Confirm → později Approve, `alt` kolize)
- focused design class — H2 (9 tříd/rozhraní)

**Cross-view issues found/resolved:** 3, opraveny v dokumentech před
změnou kódu: operace `list` v G2 bez vlastníka v H2; vlastník selhání
notifikace v G2 proti ADR-04; rozsah notifikací v G1/C2 proti H1.

**AS-IS → TO-BE delta:** 7× CHANGE (use-casy, hranice transakce, katalog
míst, `now`, vypršení, notifikace, testy souběhu), 2× KEEP (zámek SQLite,
`domain.py`), 2× VERIFY (pravidlo závislostí, doména bez I/O → L2).

**Implementation changes:** commit `1ab853e`. Nové `service.py`,
`places.py`, `notification.py`; `repository.apply` → `transaction()`; GUI
volá jen službu a kontroluje vypršení každou minutu.

**Behaviour verification:** 40 testů OK (27 domain, 2 spike, 4 souběh,
6 služba, 1 architektura). Patří k nim úspěšná cesta, kolize při Approve,
výpadek notifikace, vypršení a souběžné Approve. Mutace bez zámku shodí
3 testy souběhu. Scénář je projitý i v běžícím GUI (řízeném skriptem přes jeho tlačítka).

**Architecture conformance rule + result:** Stav `Reservation` mění jen
Reservation Management. `tests/test_architecture.py` (AST): TO-BE OK,
AS-IS `bb72a77` 7 porušení, mutace v GUI zachycena.

**Remaining uncertainty / risk:**
- vypršení i notifikace běží jen, když běží aspoň jedna instance aplikace;
- notifikace je jen `LogNotifier` bez opakování; při výpadku se ztratí;
- zámek celé SQLite DB řadí všechny zápisy. Při serverové DB otevřít ADR-04;
- role (Driver × Facility manager) se neověřují (F6);
- architektonický test nepozná volání pravidla přes alias importu
  (`import parking.domain as d`).

**Commit/tag:** větev `c03-architecture`, implementace `1ab853e`,
architektonický test `3af6de7`, tag `c03` na commitu s touto
evidencí.

## C04 — Quality & Measurement

Doplněno 2026-10-24 před C06, tedy po C05 (commit `f5daa75`). Po nálezu
C06 F1 přeměřeno v C07 se správnou zátěží QC-2, viz „Přeměření v C07“
níže; výsledek, rozhodnutí a spouštěč níže platí podle přeměření.

**Quality concern:** časové chování (ISO 25010 *performance efficiency /
time behaviour*) operace Confirm. ADR-04 řadí všechny zápisy přes zámek
celé SQLite (`BEGIN IMMEDIATE`). Uvnitř zámku `for_place` načte a
`evaluate_availability` projde všechny rezervace místa včetně historie
(riziko C05 „Lineární průchod uvnitř zámku“). Doba Confirm je tedy
i doba, po kterou čeká druhá instance (G4). Pokud čeká déle než timeout
zámku, její operace selže.

**Deklarovaný kontext:**
- 5 míst z `PlaceCatalog`, jeden soubor `parking.db` na lokálním disku;
- historie: **10 000 minulých rezervací na místo** (50 000 řádků). Je to
  předpoklad, ne data (Unknown z C01): nejvýš 4 rezervace na místo a den
  včetně nikdy nepotvrzených `DRAFT` ≈ 1 000 za rok, tedy zhruba 10 let
  bez úklidu. Stavy historie jsou smíšené (`CONFIRMED`, `CANCELLED`,
  `DRAFT`, `EXPIRED`);
- 1 instance (QC-1) a 2 instance, které potvrzují souběžně bez pauzy
  (QC-2). QC-2 je zátěžový horní odhad, ne běžné klikání v GUI.

**Evaluable criteria:**

| ID | Criterion | Zdroj hranice |
|---|---|---|
| QC-1 | p95 doby `ReservationService.confirm` ≤ **100 ms** pro 1 instanci a 10 000 rezervací/místo (200 potvrzení) | 0,1 s je hranice, kdy uživatel vnímá odezvu jako okamžitou (J. Nielsen, *Response Times: The 3 Important Limits*). Stejně dlouho čeká druhá instance na zámek. |
| QC-2 | **0** potvrzení selže na zámku ze 400 (2 instance × 200 v řadě, 10 000 rezervací/místo) | timeout zámku 5 s (výchozí `sqlite3.connect`, `LOCK_TIMEOUT` v `repository.py`). Kdo čeká déle, operace selže. |

**Measurement:** `measurements/confirm_latency.py` (jen standardní
knihovna). Pro každou velikost historie vytvoří nový SQLite soubor, naplní
historii a měří `perf_counter` kolem `service.confirm` na nových `DRAFT`.
Notifier nic nedělá, takže čas je čtení, pravidlo, zápis a `COMMIT`
v zámku, u 2 instancí navíc čekání na zámek. Volba `--index` měří
alternativu s indexem na `place_id` (jen v testovací DB).

Původní měření (`f5daa75`, Linux). **Neodpovídá QC-2 (C06 F1):** sloupec
„2-inst“ byl 2 × 100 potvrzení ve dvou vláknech jednoho procesu a „lock
errors“ i „confirmed“ počítaly i 200 solo potvrzení. Platí jen sloupce solo
(QC-1).

```
$ PYTHONPATH=src python3 measurements/confirm_latency.py --history 100 1000 10000 50000
Python 3.12.3, SQLite 3.45.1, Linux x86_64, 16 CPUs; runs=200, index on place_id: no
history/place  rows   solo p50  solo p95  solo max  2-inst p95  2-inst max  lock errors  confirmed
          100    500    5.62 ms   6.97 ms   7.66 ms     8.99 ms   737.24 ms            0  400/400
         1000   5000   11.19 ms  12.60 ms  17.98 ms    13.92 ms  1143.71 ms            0  400/400
        10000  50000   70.83 ms  80.33 ms  96.50 ms    80.97 ms  2705.80 ms            1  399/400
        50000  250000  140.86 ms 375.42 ms 411.69 ms   381.13 ms  3579.79 ms            3  397/400

$ PYTHONPATH=src python3 measurements/confirm_latency.py --index --history 10000
        10000  50000   66.61 ms  72.89 ms  81.02 ms    77.58 ms  2209.65 ms            1  399/400
```

Řádek 10 000 bez indexu je opakovaný 4× (p95 solo 78–80 ms, vždy 1 chyba
zámku). Chyba je `database is locked` po 5,0 s.

### Přeměření v C07 (C06 F1)

Skript od `ffb9c70` spouští pro QC-2 dva samostatné procesy (každý vlastní
spojení, jako dvě instance v G4), každý vytvoří a pak současně potvrzuje
200 `DRAFT`. Chyby zámku se počítají jen z těchto 400 souběžných potvrzení,
solo běh (QC-1) je zvlášť. Měřeno 2026-10-26 na Windows (lokální SSD).

```
$ PYTHONPATH=src python measurements/confirm_latency.py
Python 3.14.3, SQLite 3.50.4, Windows AMD64, 8 CPUs; runs=200, index on place_id: no
solo = 1 instance x 200 (QC-1); 2-proc = 2 processes x 200 at the same time (QC-2); 2-proc max = longest successful confirm incl. waiting for the lock
history/place  rows    solo p50   solo p95   solo max  2-proc p95  2-proc max  lock errors  confirmed
          100     500    5.16 ms    6.10 ms    7.07 ms     9.89 ms  1643.92 ms        0/400  600/600
         1000    5000    9.74 ms   11.76 ms   13.59 ms    18.66 ms  2581.68 ms        0/400  600/600
        10000   50000  101.41 ms  150.18 ms  786.09 ms    94.04 ms  3925.98 ms        2/400  598/600

$ PYTHONPATH=src python measurements/confirm_latency.py --history 10000     # 5 dalších běhů
        10000   50000   41.22 ms   57.75 ms  112.93 ms    78.54 ms  1065.55 ms        2/400  598/600
        10000   50000   41.56 ms   45.79 ms   72.62 ms    77.24 ms  1503.04 ms        2/400  598/600
        10000   50000   42.26 ms   47.32 ms   55.52 ms    70.22 ms  5641.26 ms        1/400  599/600
        10000   50000   60.17 ms   66.70 ms   80.72 ms    62.57 ms  4045.34 ms        1/400  599/600
        10000   50000   41.59 ms   54.05 ms   75.20 ms    67.87 ms  5098.15 ms        1/400  599/600

$ PYTHONPATH=src python measurements/confirm_latency.py --index --history 10000
        10000   50000   35.15 ms   42.13 ms   55.09 ms    42.04 ms  1923.26 ms        1/400  599/600

$ PYTHONPATH=src python measurements/confirm_latency.py --history 50000
        50000  250000  205.14 ms  229.94 ms  426.79 ms   234.18 ms  3380.97 ms        7/400  593/600
```

Kontrola na druhém stroji (2026-11-02, Linux, čistý checkout `0c263f0`, příkazy
podle README):

```
$ PYTHONPATH=src python3 measurements/confirm_latency.py
Python 3.12.3, SQLite 3.45.1, Linux x86_64, 16 CPUs; runs=200, index on place_id: no
history/place  rows    solo p50   solo p95   solo max  2-proc p95  2-proc max  lock errors  confirmed
          100     500    5.38 ms    6.22 ms    6.70 ms     8.18 ms  1439.19 ms        0/400  600/600
         1000    5000    8.67 ms    9.63 ms   12.53 ms    11.80 ms  2146.19 ms        0/400  600/600
        10000   50000   26.78 ms   48.93 ms   55.45 ms    46.41 ms  1265.55 ms        1/400  599/600

$ PYTHONPATH=src python3 measurements/confirm_latency.py --history 10000     # 3 další běhy
        10000   50000   27.13 ms   49.70 ms   55.94 ms    50.16 ms  1261.62 ms        1/400  599/600
        10000   50000   26.86 ms   30.19 ms   31.90 ms    49.67 ms  1161.83 ms        1/400  599/600
        10000   50000   27.06 ms   30.05 ms   32.01 ms    46.57 ms  1264.87 ms        1/400  599/600
```

Tentýž stroj dal v C04 solo p95 77–80 ms. Solo část skriptu ani kód Confirm se
od té doby nezměnily, příčina rozdílu není ověřená (pravděpodobně zatížení
stroje), proto uvádíme rozsah, ne jedno číslo.

Kolísání QC-1 na Windows (C07 P2, 2026-11-02, skript od `322bc58`, měření
se nezměnilo). Stroj: notebook AMD Ryzen 7 4700U (8 jader), 15,4 GB RAM,
NVMe SSD, Windows 11 IoT Enterprise LTSC 10.0.26100, plán napájení
Balanced, **připojený adaptér**, během měření nic dalšího neběželo:

```
$ PYTHONPATH=src python measurements/confirm_latency.py --history 10000     # 10 běhů
Python 3.14.3, SQLite 3.50.4, Windows AMD64, 8 CPUs; runs=200, index on place_id: no
history/place  rows    solo p50   solo p95   solo max  2-proc p95  2-proc max  lock errors  confirmed
        10000   50000   39.47 ms   52.93 ms   89.05 ms    48.38 ms  2525.43 ms        1/400  599/600
        10000   50000   38.00 ms   40.90 ms   59.73 ms    53.13 ms  2351.99 ms        1/400  599/600
        10000   50000   37.98 ms   56.58 ms   69.11 ms    57.77 ms  3018.90 ms        1/400  599/600
        10000   50000   37.97 ms   55.57 ms   77.31 ms    47.19 ms  2627.70 ms        1/400  599/600
        10000   50000   50.00 ms   57.26 ms   86.69 ms    82.74 ms  5690.77 ms        1/400  599/600
        10000   50000   37.49 ms   39.79 ms   59.77 ms    41.79 ms  2291.21 ms        1/400  599/600
        10000   50000   37.50 ms   39.80 ms   53.00 ms    45.04 ms  2392.90 ms        1/400  599/600
        10000   50000   38.02 ms   41.10 ms   49.44 ms    42.18 ms  2311.42 ms        1/400  599/600
        10000   50000   37.47 ms   39.89 ms   51.37 ms    42.03 ms  2244.69 ms        1/400  599/600
        10000   50000   37.85 ms   40.52 ms   59.31 ms    41.80 ms  2422.49 ms        1/400  599/600
```

Na adaptéru je QC-1 p95 39,8–57,3 ms v 10 z 10 běhů (rezerva ≥ 43 %),
nejhorší jednotlivé potvrzení 89 ms. Bezprostředně před měřením běžel
notebook na baterii. Stav napájení u běhů z 2026-10-26 zapsaný není, proto
je napájení (škrcení CPU na baterii) **pravděpodobná, ne ověřená** příčina
odlehlého běhu 150 ms. Pravidlo pro další měření: na adaptéru a se
zapsaným stavem napájení.

„2-proc max“ je nejdelší *úspěšné* potvrzení. Že může přesáhnout 5 s,
pravděpodobně způsobuje to, že busy handler SQLite započítává do timeoutu
součet svých plánovaných uspání, ne skutečně uplynulý čas (neověřeno).

**Výsledek** (platí od C07):

| Criterion | Naměřeno (10 000/místo) | Výsledek |
|---|---|---|
| QC-1 p95 ≤ 100 ms | Linux 77–80 ms ve 4 bězích (C04) a 30–50 ms ve 4 bězích (2026-11-02); Windows 2026-10-26 46–67 ms v 5 bězích ze 6, jeden běh 150 ms (max 786 ms); Windows 2026-11-02 na adaptéru 40–57 ms v 10 z 10 běhů; hodnotitelé C06 na Windows 94,7 ms | **splněno ve 23 ze 24 vlastních běhů**; odlehlý běh pravděpodobně na baterii (neověřeno). Rezerva na Windows hodnotitelů jen 5 %. Při 50 000/místo 230 ms |
| QC-2 0 chyb zámku ze 400 souběžných | Windows 1–2/400 v každém ze 16 běhů; Linux 1/400 v každém ze 4 běhů; hodnotitelé C06 2–3/400 | **nesplněno**. Při 1 000/místo 0/400 (náš běh i hodnotitelé), při 50 000/místo 7/400 |

Co měření ukázalo navíc:
- čas roste s počtem rezervací místa, ne s SQL dotazem. Index na
  `place_id` zlepšil p95 v jednom běhu o 10–25 % (42 ms proti 46–67 ms) a
  chyby zámku nezměnil (1/400). Většinu času stojí převod řádků na
  `Reservation` a průchod v Pythonu;
- chyby u QC-2 nejsou dlouhé jednotlivé transakce (p95 souběžně 62–94 ms).
  Je to nespravedlivé čekání na zámek SQLite: držitel zámek uvolní a hned
  ho znovu vezme, čekající mezitím spí v busy handleru;
- nejdelší čekání při 1 000/místo je už 2,6 s, tedy přes polovinu
  `LOCK_TIMEOUT`. Rezerva spouštěče „> 1 000/místo“ je malá;
- před C04 byla tahle chyba `sqlite3.OperationalError`, kterou GUI
  nechytalo (`_guard` chytal jen `RuleViolation` a `ValueError`).
  Uživatel nedostal hlášku. Stav zůstal správně beze změny.

**Trade-off / decision** (D4 v `docs/architecture-and-decisions.md`):
- **ADR-04 se nemění, index se nepřidává.** QC-1 platí i bez indexu a
  index QC-2 nesplní, jen přidá schéma.
- **Nesplněné QC-2 přijímáme pro deklarovaný horní odhad.** Nastává až při
  10 000 rezervacích na místo a nepřetržitém souběhu dvou instancí. Do
  1 000 rezervací na místo (zhruba první rok podle předpokladu výše) platí
  obě kritéria, i se správnou zátěží.
- **Selhání zámku je definovaný výsledek:** `transaction()` vyhodí
  `StoreBusy`, nic se nezměnilo a GUI ukáže „try again“. Týká se i Create,
  který zapisuje přes `transaction()`, a od `3021f97` i kontroly vypršení
  v `refresh()` (C06 F2): po akci tlačítka GUI ukáže „Busy“, časovač jen
  odloží kontrolu na další minutu. Testy
  `test_concurrency.test_lock_timeout_is_store_busy_and_changes_nothing`
  a `test_concurrency.ExpiryWhileStoreBusy`.
- **Spouštěč pro znovuotevření ADR-04:** víc než 1 000 rezervací na místo
  (poslední naměřený bod, kde QC-2 platí se správnou zátěží, potvrzený
  i hodnotiteli), nebo první chyba zámku v měření 2 procesů × 200 při
  aktuálním objemu. Nejdelší čekání sledujeme jako indikátor: při 1 000/místo
  je už 2,6 s z 5 s. Pak úklid historie nebo filtr v SQL podle stavu
  a intervalu.

**Zbývající mezery:** QC-2 se správnou zátěží je měřené na jednom stroji
s Windows a jednom s Linuxem, oba lokální SSD; síťový disk měřen není. Běh
QC-1 na Windows 150 ms má jen pravděpodobné vysvětlení (napájení z baterie,
C07 P2); rozdíl mezi linuxovými měřeními (77–80 ms proti 30–50 ms) je
nevysvětlený. Historie je syntetická a objem je předpoklad, ne údaj provozu.

## C05 — Detailed Design Evidence

Detailní návrh je v `docs/architecture-and-decisions.md`, sekce
„C05 Detailed Design“ (DD-1 až DD-9). Řetěz: Reservation Management (G2)
→ DD-1..DD-9 → `domain.py` (`bbe817b`) → `tests/test_detailed_design.py`
(`91a9ed1`).

**Selected unit:** *Availability & Confirmation decision* uvnitř
Reservation Management: `evaluate_availability`, `is_available`,
`Reservation.overlaps(_interval)`, `confirm`, `approve`,
`_require_not_started`, `_require_no_conflict` (DD-1). Hraniční prvek je
`ReservationService`. Architektura ani ADR-04 se nemění.

**Návrhová změna (DD-4):** dostupnost vrací výsledek a důvod
(`AvailabilityResult(outcome, conflicts)`, `Availability.AVAILABLE |
UNAVAILABLE | INVALID_INTERVAL`) místo `bool`. `confirm` a `approve`
hlásí v `RuleViolation` id kolidujících rezervací. `is_available` zůstává
odpovědí OP-02 ano/ne.

### Tests derived from DD

Unit = čisté funkce `domain.py`. Component = `ReservationService` se
skutečným `ReservationRepository` nad dočasnou SQLite. Integration =
souběh dvou spojení nad jedním souborem.

| DD rozhodnutí / DT-x | Test (`tests/test_detailed_design.py`, není-li uvedeno jinak) | Úroveň |
|---|---|---|
| DT-A1 navazuje zleva | `DTAvailability.test_DT_A1_touching_on_the_left_is_available` | unit |
| DT-A2 navazuje zprava | `DTAvailability.test_DT_A2_touching_on_the_right_is_available`; dotaz 09:00–10:00 v `DDServiceCollaboration.test_DT_A8_only_confirmed_rows_in_the_store_block_check` | unit, component |
| DT-A3 částečný překryv | `DTAvailability.test_DT_A3_partial_overlap_is_unavailable` | unit |
| DT-A4 dotaz uvnitř | `DTAvailability.test_DT_A4_query_inside_is_unavailable` | unit |
| DT-A5 dotaz obsahuje | `DTAvailability.test_DT_A5_query_containing_is_unavailable` | unit |
| DT-A6 shodný interval | `DTAvailability.test_DT_A6_same_interval_is_unavailable` | unit |
| DT-A7 jiné místo | `DTAvailability.test_DT_A7_confirmed_on_another_place_does_not_block` | unit |
| DT-A8 blokuje jen `CONFIRMED` | `DTAvailability.test_DT_A8_only_confirmed_blocks` (5 stavů jako `subTest`); `DDServiceCollaboration.test_DT_A8_only_confirmed_rows_in_the_store_block_check` (`DRAFT` a `CANCELLED` v DB) | unit, component |
| DT-A9 / DR-2 vyloučení sebe sama | `DTAvailability.test_DT_A9_excluded_reservation_does_not_block_itself`; `DTConfirm.test_DR2_stored_copy_of_itself_does_not_block` | unit |
| DT-A10 všechny kolize v pořadí `existing` | `DTAvailability.test_DT_A10_all_conflicts_in_existing_order` | unit |
| DT-A11 neplatný interval | `DTAvailability.test_DT_A11_invalid_interval_is_an_outcome_before_any_comparison`, `DD4IsAvailable.test_DT_A11_invalid_interval_gets_no_answer`, `DTConfirm.test_DT_A11_…`, `DTApprove.test_DT_A11_…`; `DDServiceCollaboration.test_DT_A11_check_rejects_an_invalid_interval` | unit, component |
| DT-A12 prázdné `existing` | `DTAvailability.test_DT_A12_no_reservations_is_available` | unit |
| DD-4 Post, BR-01 pro všechny polohy intervalů | `DTAvailability.test_DD4_BR01_matches_a_shared_hour_on_a_grid` (100 dvojic intervalů proti nezávislému orákulu „sdílí hodinový slot“) | unit |
| DD-4 Post: `existing` beze změny, výsledek je neměnná hodnota | `DTAvailability.test_DD4_existing_reservations_are_not_changed`, `test_DD4_result_is_an_immutable_value` | unit |
| DD-4 Pre: aware datetime, porušení = `TypeError`, stav beze změny | `DTAvailability.test_DD4_naive_datetime_violates_the_precondition`, `DTConfirm.test_DD4_naive_now_violates_the_precondition_and_changes_nothing` | unit |
| DR-6 `is_available` = `AVAILABLE` | `DD4IsAvailable.test_DD4_available_is_true_unavailable_is_false` | unit |
| DT-C1 confirm, volné místo | `DTConfirm.test_DT_C1_free_place_confirms`; `DDServiceCollaboration.test_DT_C1_for_place_contains_the_reservation_itself_and_confirm_still_succeeds` | unit, component |
| DT-C2 kolize pojmenovaná, zůstává `DRAFT` | `DTConfirm.test_DT_C2_conflict_is_named_and_draft_stays` | unit |
| DT-C3 místo se schválením | `DTConfirm.test_DT_C3_approval_place_goes_pending` | unit |
| DT-C4 kolize odložena na approve | `DTConfirm.test_DT_C4_approval_place_defers_the_conflict_check`; `DDServiceCollaboration.test_DT_C4_DT_P2_conflict_is_deferred_to_approve_and_named_there` | unit, component |
| DT-C5 + DD-8 čas před kolizí | `DTConfirm.test_DT_C5_at_or_after_start_is_too_late_even_with_a_conflict` (`now == start` i po něm, obě místa) | unit |
| DT-C6 + DD-8 stav před časem | `DTConfirm.test_DT_C6_only_draft_can_be_confirmed_and_state_is_checked_first` (5 stavů × před/po `start`) | unit |
| DT-P1 jiné žádosti neblokují | `DTApprove.test_DT_P1_no_confirmed_conflict_confirms_other_requests_do_not_block` | unit |
| DT-P2 všechny kolize pojmenované, zůstává `PENDING_APPROVAL` | `DTApprove.test_DT_P2_every_conflict_is_named_and_request_stays_pending`; `DDServiceCollaboration.test_DT_C4_DT_P2_…` (stav v DB, rollback, bez notifikace) | unit, component |
| DT-P3 po `start` | `DTApprove.test_DT_P3_at_or_after_start_is_too_late_even_with_a_conflict` | unit |
| DT-P4 + DD-8 stav před časem | `DTApprove.test_DT_P4_only_pending_approval_can_be_approved_and_state_is_checked_first` | unit |
| REQ-04, REQ-06: pravidlo + zámek SQLite při souběhu | `tests/test_concurrency.py` (4 testy, beze změny z C03) | integration |

**Proč tato úroveň** (doplněno v C07 podle připomínky C06 k D3):

| Test | Selected level | Why this level | What design decision |
|---|---|---|---|
| `DTAvailability.test_DT_A1_touching_on_the_left_is_available` | unit | rozhodnutí je jen porovnání intervalů v čisté funkci `evaluate_availability`; úložiště ani hodiny výsledek nemění, takže vyšší úroveň by přidala jen šum | DT-A1, DR-4: interval `[start, end)`, navazující rezervace nekoliduje (BR-01) |
| `DDServiceCollaboration.test_DT_C4_DT_P2_conflict_is_deferred_to_approve_and_named_there` | component | výsledek závisí na tom, co vrátí skutečné `for_place` ze SQLite, na rollbacku transakce a na tom, že se při chybě nenotifikuje; to unit test s ručně sestaveným `existing` neověří | DT-C4 (kolize se odkládá na approve), DT-P2 (kolize pojmenovaná, stav zůstává), ADR-04 (hranice transakce a notifikace po commitu) |
| `test_concurrency.test_two_concurrent_conflicting_confirmations_confirm_at_most_one` | integration | souběh dvou instancí vzniká až mezi dvěma spojeními nad jedním souborem; zámek `BEGIN IMMEDIATE` je vlastnost SQLite, ne kódu jednotky | REQ-04, DD-6: kontrola kolize a zápis v jedné transakci |

### Unit/component tests executed

Příkaz: `PYTHONPATH=src python -m unittest discover -s tests -v`
(PowerShell: `$env:PYTHONPATH="src"; python -m unittest discover -s tests -v`).

- celkem **76 testů, OK**: 40 z C03 beze změny (27 `test_domain`,
  2 `test_persistence_spike`, 4 `test_concurrency`, 6 `test_service`,
  1 `test_architecture`) + **36 nových** v `test_detailed_design`
  (32 unit, 4 component);
- `test_architecture` (ADR-04) projde i po změně `domain.py`. Nové typy
  i funkce jsou v `domain.py` a žádný import nepřibyl.

```
$ PYTHONPATH=src python -m unittest discover -s tests
............................................................................
----------------------------------------------------------------------
Ran 76 tests in 1.300s

OK
```

**Mutace** (vždy jedna změna v `domain.py`, spuštěna celá sada, soubor
vrácen, `git status` čistý):

| # | Mutace (rozbité rozhodnutí DD) | Selhalo testů | Z toho nové (`test_detailed_design`) |
|---|---|---|---|
| M1 | `overlaps_interval`: `<` → `<=` (DT-A1, DT-A2) | 8 | 5 |
| M2 | filtr bez `State.CONFIRMED` (DT-A8, DT-P1) | 9 | 4 (+ 2 souběh, 2 domain, 1 service) |
| M3 | filtr bez `exclude_id` (DR-2, DT-A9) | 2 | 2 — **jen nové testy** |
| M4 | filtr bez `place_id` (DT-A7) | 1 | 1 — **jen nový test** |
| M5 | confirm kontroluje kolizi před větví `requires_approval` (DT-C4) | 3 | 2 |
| M6 | confirm kontroluje čas před stavem (DD-8, DT-C6) | 1 | 1 — **jen nový test** |
| M7 | hranice `now >= start` → `now > start` (DT-C5, DT-P3) | 5 | 2 |
| M8 | `evaluate_availability` bez kontroly `end <= start` (DT-A11) | 6 | 5 |
| M9 | `RuleViolation` bez id kolizí, tj. chování z C03 (DT-C2, DT-P2) | 3 | 3 — **jen nové testy** |

Každou mutaci zachytí aspoň jeden test odvozený z DD. M3, M4, M6 a M9
by bez C05 testů prošly, protože C03 sada tato rozhodnutí neověřovala.

### Design ↔ implementation mismatch

Kontrola rozhodnutí DD-1..DD-9 proti kódu (review, testy, mutace).
Kód se upravoval směrem k návrhu. Návrh jsme upravili jen tam, kde
popisuje skutečné a zdůvodněné chování, nikdy ne podle chybného kódu.

| DD rozhodnutí | Doklad (test / review / mutace) | Výsledek | Akce (keep / update design / update code) |
|---|---|---|---|
| DD-4 výsledek nese důvod (DR-5, DR-10) | review C03: `is_available` vracel jen `bool`, `confirm`/`approve` hlásily kolizi bez id; M9 | nesoulad | **update code** `bbe817b`: `evaluate_availability`, `AvailabilityResult`, `_require_no_conflict` s id ve zprávě |
| DD-3/DD-4 vstupem je interval, ne rezervace (DR-4) | review: `is_available` tvořil pomocnou `Reservation(place_id, "", start, end)` s novým `uuid` jen kvůli `overlaps` | nesoulad (falešná rezervace bez uživatele) | **update code** `bbe817b`: `Reservation.overlaps_interval`, `overlaps` na ni deleguje |
| DD-3 BR-02 při vzniku alokace má jednoho vlastníka (DR-10) | review: `confirm` i `approve` měly vlastní kopii filtru `others` a volání `is_available` | duplicita | **update code** `bbe817b`: obě volají `_require_no_conflict` s `exclude_id` |
| DD-4 Pre: datetime jsou timezone-aware | `test_DD4_naive_*` (2 testy): `TypeError`, stav beze změny | kód tak funguje, C02/C03 to neříkaly | **update design** (DD-4 Pre a Failures, `2764ac8`), **keep code**: smíchání naive a aware je chyba volajícího, ne porušení pravidla, a uživatel ji nemá dostat jako `RuleViolation` |
| DR-2 vyloučení sebe sama, DR-3 filtr místa | M3 a M4 chytí jen nové unit testy (DT-A9, DR-2, DT-A7), žádný test služby | v tocích služby jsou filtry jen obranné: `for_place` vrací jedno místo, vlastní kopie je `DRAFT` / `PENDING_APPROVAL` | **update design** (odstavec v DD-4, commit s touto evidencí), **keep code**: kontrakt nesmí záviset na tom, co volající do `existing` dá |
| DT-C4 confirm na místě se schválením nekontroluje kolizi | DT-C4 (unit i component), M5 | soulad, dřív jen v docstringu a REQ-03 v0.2 | **keep**, teď explicitně v DD-8 |
| DD-8 pořadí kontrol stav → čas → schválení → kolize | DT-C5, DT-C6, DT-P3, DT-P4; M6 chytí jen DT-C6 | soulad | **keep** |
| DD-4 Pre `place.id == reservation.place_id` | review `confirm` | kód ji nevynucuje | **keep**: jediný volající je `ReservationService` (ADR-04, hlídá `test_architecture`) a místo bere z katalogu podle `r.place_id` |
| DD-4 neplatný interval: `evaluate_availability` vrací hodnotu, ostatní odmítají | `test_invalid_interval_is_rejected` (C02), DT-A11 (5 testů), M8 | soulad | **keep** |
| DT-A1..A12, DT-C1..C6, DT-P1..P4 | 36 testů OK | soulad po změně kódu | **keep** |
| `expire_if_due` vrací `bool`, `ReservationService.expire_due` ho nečte | review `service.py` | mimo hranici DD-1 | **keep**: služba po přechodu filtruje podle stavu, protože o žádosti mohla mezitím rozhodnout jiná instance (C03). `bool` čte `test_domain` |
| H2 (C03) nezná `evaluate_availability` | review | H2 je pohled C03, DD-5 je zoom do stejného modulu | **keep** H2, DD-5 na něj odkazuje |

### Remaining design risk

- **Lineární průchod uvnitř zámku.** `for_place` vrací rezervace místa ve
  všech stavech včetně historie a `DRAFT`, které po `start` zůstávají
  navždy. S historií roste doba, po kterou transakce drží zámek celé
  SQLite (ADR-04). Zmírnění by bylo filtrovat v SQL podle stavu a
  intervalu, tím by se ale část BR-02 přesunula do Reservation Store.
  Otevřít s ADR-04. Změřeno v C04 (QC-1, QC-2), spouštěč je víc než
  1 000 rezervací na místo.
- **Časová zóna jen jako precondition.** HTTP adaptér z CP1 musí převést
  vstup na aware UTC. Jinak dostane klient `TypeError` (chyba 500) místo
  chyby pravidla. *C07: ošetřeno v `http_api.py` (`1670c5c`), čas bez
  offsetu je 400, test `test_time_without_utc_offset_is_400_and_nothing_is_stored`.*
- **`approve` neověřuje `requires_approval` znovu.** Když se katalog změní
  mezi confirm a approve, rozhoduje stav žádosti, ne aktuální místo.
- **Hláška kolize obsahuje id cizí rezervace.** Dnes GUI ukazuje všechny
  rezervace. Až budou role (F6), je potřeba rozhodnout, co smí Driver
  vidět.
- **Role se neověřují (F6).** `approve` může vyvolat kdokoli.
- **Rozsah příkladů.** Dostupnost je ověřená tabulkou DT-A a hodinovou
  mřížkou (všechny vzájemné polohy dvou intervalů). Hranice pod minutu
  (sekundy, mikrosekundy) zvlášť testované nejsou. Pravidlo je ale jen
  porovnání `datetime`, takže na přesnosti nezávisí.

### Commit/tag

Větev `c05-detailed-design` (z `c03-architecture`). DD před kódem:
`2764ac8` (DD-1..DD-4), `a2f10de` (DD-5..DD-9). Implementace `bbe817b`,
testy `91a9ed1`. Evidence a doplnění DD-4 z kontroly kódu `29d9382`
(tag `c05`). Sloučeno do `main` přes PR #7 (`5ba2b09`).

## C06 — Nálezy nezávislého assessmentu a jejich vypořádání

Hodnotil tým VoltBook (J. Staněk, D. Volný, Š. Exner) 2026-10-25 nad
baseline `c06-baseline` = `a238f54` (Windows 11, Python 3.14.7, SQLite
3.50.4). Výsledek: B1–B2, R1–R5, A1–A4 a D1–D5 YES; Q1 YES, Q2 a Q3
PARTLY. Tři candidate findings, dva ověřené.

| ID | Claim (C01–C05) | Expected → Observed | Verify | Disposition | Action / closure evidence |
|---|---|---|---|---|---|
| F1 | C04 QC-2: „0 chyb zámku ze 400 (2 instance × 200)“, výsledek „1 ze 400“ | 2 procesy × 200 souběžných potvrzení → skript pouštěl 2 × 100 ve dvou vláknech jednoho procesu a do „ze 400“ počítal 200 solo potvrzení (`confirm_latency.py:94-125` na `a238f54`) | potvrzeno čtením skriptu; hodnotitelé se správnou zátěží naměřili 2–3/400 chyb | VERIFIED FINDING → **ACCEPT**. Závěr „QC-2 při 10 000/místo nesplněno“ platil, chybná byla kvantifikace a spouštěč v D4 | skript měří 2 procesy × 200 (`ffb9c70`); přeměřeno, opravena evidence C04, D4 a spouštěč ADR-04 (viz C04 výše). **Uzavřeno** |
| F2 | D4: „StoreBusy, nic se nezměnilo, GUI ukáže try again“ | každý zápis z GUI skončí hláškou → zápis vypršení v `refresh()` (`gui.py:91-94` na `a238f54`) byl mimo try/except, StoreBusy utekl z Tk callbacku | reprodukováno testem se zamčenou DB; na původním `gui.py` test padá na `StoreBusy` | VERIFIED FINDING → **ACCEPT**. Data zůstávala konzistentní, chyběla slíbená reakce GUI | `refresh()` chytá StoreBusy a tabulku překreslí, `_guard` ukáže „Busy“, `_tick` jen odloží vypršení (`3021f97`); testy `test_concurrency.ExpiryWhileStoreBusy` (2). **Uzavřeno** |
| F3 | testy souběhu odhalí chybějící zámek (REQ-04, REQ-06) | podezření, že `SlowRepository` zpomaluje jen `save` → mutace: deferred `BEGIN` 4 z 5 FAIL, bez `transaction()` 3 FAIL + 1 ERROR | mutace hodnotitelů | REJECTED CANDIDATE | no-change; poznámka o 5 testech doplněna k L1 v `architecture-and-decisions.md` |

Drobnosti z formuláře, opravené v C07: evidence C04 neuváděla commit (je to
`f5daa75`), poslední uvedený počet testů byl 76 (baseline měla 78, po C07
je 80), README mělo jen bash syntaxi (doplněn PowerShell), u D3 chybělo
zdůvodnění úrovně testů (tabulka „Proč tato úroveň“ v C05).

Testy po opravách: `PYTHONPATH=src python -m unittest discover -s tests`
→ `Ran 80 tests — OK` (78 z baseline + 2 nové pro F2).

Review oprav F2 (`3021f97`) a F1 (`ffb9c70`): O. Malinák, 2026-11-02, s pomocí
Claude Code. Čistý checkout `0c263f0` na Linuxu: 80 testů OK, žádný přeskočený.
S `gui.py` z `3021f97^` skončí
`test_gui_reports_a_busy_expiry_check_and_keeps_running` chybou `StoreBusy`,
ostatní testy projdou. Skript měří 2 procesy × 200, chyby počítá jen ze
souběžných potvrzení a solo běh hlásí zvlášť. Připomínka bez změny kódu: když
proces měření spadne před bariérou (např. `StoreBusy` při vytváření `DRAFT`),
skript se zablokuje. Týká se jen měření, ne aplikace; zatím se to nestalo.
Opraveno v `322bc58` (viz „C07 — Otevřené položky po review“).

## C07 — Otevřené položky po review (2026-11-02)

**Čistý checkout na Windows** (doplnění k R-03): `git clone` větve `main` na
`63cbc0e`, příkazy z README pro PowerShell
(`$env:PYTHONPATH = "src"; python -m unittest discover -s tests`) →
`Ran 80 tests in 2.501s — OK`, žádný přeskočený. Windows 11 IoT Enterprise
LTSC 10.0.26100, Python 3.14.3, SQLite 3.50.4.

**Měřicí skript se už nezablokuje** (připomínka z review, `322bc58`): proces,
který selže, to ohlásí rodiči a zruší bariéru, takže druhý proces nečeká
navždy; bariéra má navíc timeout a rodič pozná i proces, který skončil bez
výsledku. Měření pak skončí `RuntimeError` s popisem. Ověřeno ručně:
dvě zdravé instance měří dál; jedna instance neotevře databázi → chyba po
0,3 s; obě dostanou `StoreBusy` při vytváření `DRAFT` → chyba po 5,8 s.

**HTTP API, CP1 walking skeleton** (R-04, `1670c5c`). Tým rozhodl, že CP2
zahrnuje HTTP klienta, takže R-04 přešlo z AVOID na MITIGATE. `http_api.py`
je tenký klient nad `ReservationService` jako GUI (ADR-04, AD-04, D5):

```
POST /reservations {place_id, user_id, start, end} -> 201 {id, state: "DRAFT"}
GET  /reservations/<id>                           -> 200 uložená rezervace
400 vadný požadavek nebo čas bez UTC offsetu, 422 porušené pravidlo,
503 + Retry-After při StoreBusy, 404 neznámé id nebo cesta
```

Každý požadavek otevře vlastní službu a spojení jako další instance v G4,
takže zámek SQLite řadí i API proti GUI. Čas bez offsetu je 400, ne
`TypeError` (riziko C05 „Časová zóna jen jako precondition“ je tím pro HTTP
ošetřené). Architektonický test hlídá oba klienty (`gui.py`, `http_api.py`).
Spuštění: `python3 run_api.py [parking.db] [--port 8000]`.

Testy `tests/test_http_api.py` (8): kontrola z README end to end (POST →
`DRAFT` → GET podle id), služba na straně GUI potvrdí rezervaci vytvořenou
přes HTTP, převod offsetu na UTC, 400 pro čas bez offsetu / vadný JSON /
chybějící pole (nic se neuloží), 422 pro BR-04 a neznámé místo, 404.
Mutace: import repozitáře v `http_api.py` shodí architektonický test;
bez kontroly offsetu spadne test 400. Ručně: `run_api.py` + `curl` POST/GET.

Testy celkem: `Ran 88 tests — OK` (80 + 8 pro HTTP API).

## C07 — Risk register

Klasifikace před ošetřením: L a I 1–3, priorita L × I (1–2 LOW, 3–4
MEDIUM, 6–9 HIGH). Actual residual se snižuje jen s ověřeným opatřením,
jinak NOT VERIFIED. Stav k 2026-11-02, větev `c07-open-items` (nad
`63cbc0e`).

### R-01
- statement: Implementaci a evidenci C02–C04 (`service.py`, `test_concurrency.py`, `confirm_latency.py`; 24 ze 40 commitů) napsal hlavně jeden člen → při jeho nedostupnosti nikdo jiný neopraví nálezy ani nezopakuje měření → uzavření C06 findings a příprava CP2 se zpozdí.
- category: People / Resources
- initial: L=2, I=3 → HIGH (6); zastupitelnost nebyla vyzkoušena, C05 také vytvořil jediný člen.
- strategy: MITIGATE – omezit single point of failure.
- plan: opravy F1 a F2 udělá jiný člen než autor původního kódu, s review autora; čistý checkout + testy + měření podle README na druhém stroji (Windows). Owner: R. Gřegoř.
- evidence: F1 a F2 opravil R. Gřegoř (`3021f97`, `ffb9c70`), měření proběhlo na jeho stroji s Windows. Review autorem původního kódu: O. Malinák 2026-11-02 (viz C06), včetně čistého checkoutu, testů a měření na Linuxu.
- expected: L=1, I=2 → LOW (2)
- actual: L=1, I=2 → LOW (2) – oprava i měření jiným členem a review proběhly; zastupitelnost je ověřená na opravách F1 a F2.
- acceptance: přijato R. Gřegořem za tým, rozhodnuto 2026-10-30 s podmínkou review; podmínka splněna review O. Malináka 2026-11-02.
- monitor: trigger – PR k opravě bez review od jiného člena nebo selhání čistého buildu; přezkum na C08.

### R-02
- statement: ADR-04 řadí zápisy zámkem celé SQLite a Confirm v zámku prochází celou historii místa → se 2 instancemi a rostoucí historií čeká potvrzení déle než `LOCK_TIMEOUT` 5 s → operace skončí StoreBusy a uživatel musí opakovat (BR-02 zůstává splněno).
- category: Technical / Quality (C04 QC-2, riziko C05 „lineární průchod uvnitř zámku“)
- initial: L=2, I=2 → MEDIUM (4); objem historie je předpoklad (Unknown z C01), ne provozní údaj.
- strategy: ACCEPT (D4) s monitoringem; ADR-04 a schéma beze změny.
- plan: přijetí opřít o měření ve správném kontextu (F1). Owner: R. Gřegoř.
- evidence: přeměřeno se 2 procesy × 200 (C04 výše): 1–2/400 chyb při 10 000/místo, 0/400 při 1 000/místo, nejdelší čekání při 1 000/místo 2,6 s.
- expected: L=2, I=2 → MEDIUM (4) – přijetí riziko nesnižuje.
- actual: MEDIUM (4), přijato na základě nového měření.
- acceptance: tým pro CP2; contingency: filtr v SQL podle stavu a intervalu nebo úklid historie (C05).
- monitor: trigger – víc než 1 000 rezervací na místo, nebo první chyba zámku v měření 2 procesů × 200 při aktuálním objemu (D4); indikátor – nejdelší čekání na zámek; přezkum při každém novém měření.

### R-03
- statement: Evidence C04 a návod ke spuštění vznikly na jednom vývojovém stroji (Linux, README jen v bash syntaxi) → na demonstračním stroji s Windows se příkazy podle README nespustí nebo se QC-1 nepotvrdí → tvrzení QC-1 nelze při obhajobě reprodukovat.
- category: Release / Operation
- initial: L=2, I=2 → MEDIUM (4); hodnotitelé na Windows naměřili QC-1 p95 94,7 ms (rezerva 5 %).
- strategy: MITIGATE
- plan: README s PowerShell příkazy; měření i na Windows, výsledky uvádět po prostředích. Owner: R. Gřegoř.
- evidence: README doplněno; čistý checkout `63cbc0e` podle README na Windows: 80 testů OK (viz „C07 — Otevřené položky po review“). QC-1 na Windows: 2026-10-26 5 z 6 běhů 46–67 ms a jeden 150 ms; 2026-11-02 na adaptéru 10 z 10 běhů 40–57 ms (C04 výše). Trigger (> 90 ms) nastal během 150 ms; příčina je pravděpodobně napájení z baterie (neověřeno).
- expected: L=1, I=2 → LOW (2)
- actual: LOW (2) – spuštění podle README ověřené čistým checkoutem, QC-1 na Windows na adaptéru stabilně s rezervou ≥ 43 %. Platí s podmínkou měřit a obhajovat na adaptéru; výsledek hodnotitelů 94,7 ms (jiný stroj) zůstává jako upozornění.
- acceptance: R. Gřegoř za tým, 2026-11-02; contingency: obhajovat na stroji, kde je měření doložené, a na síťovém napájení.
- monitor: trigger – p95 QC-1 > 90 ms na kterémkoli vykázaném stroji (rezerva < 10 %) při měření na adaptéru; u každého měření zapsat stav napájení.

### R-04
- statement: README plánuje HTTP adaptér (CP1 walking skeleton, AD-04), který neexistuje; DD-4 předpokládá vstup jako aware UTC → adaptér psaný těsně před CP2 → `TypeError` místo chyby pravidla, neověřený druhý proces nad SQLite, nehotová demonstrace.
- category: Schedule / Integration
- initial: L=2, I=2 → MEDIUM (4); adaptér nezačat, termín CP2 v plánu týmu chybí.
- strategy: MITIGATE – tým 2026-11-02 rozhodl, že CP2 zahrnuje HTTP klienta (původní návrh AVOID tím odpadl).
- plan: tenký adaptér nad `ReservationService` + E2E test hned, ne těsně před CP2. Owner: R. Gřegoř.
- evidence: `1670c5c` – `POST /reservations` a `GET /reservations/<id>`, 8 testů v `test_http_api.py` včetně kontroly z README a času bez offsetu (400); architektonický test hlídá i `http_api.py` (viz „C07 — Otevřené položky po review“).
- expected: L=1, I=2 → LOW (2)
- actual: LOW (2) – adaptér je integrovaný a ověřený testy. Termín CP2 tým dál nezná (planning gap ve status reportu, ne toto riziko).
- acceptance: R. Gřegoř za tým, 2026-11-02.
- monitor: trigger – CP2 bude požadovat další operace přes HTTP (confirm, approve, cancel) nebo přihlášení; pak nové rozhodnutí o rozsahu a R-05.

### R-05
- statement: Role se neověřují (C03 F6) a hláška kolize ukazuje id cizí rezervace → při víceuživatelském provozu schválí VIP žádost neoprávněná osoba → obejití schvalování v0.2.
- category: Security / Privacy
- initial: L=1, I=2 → LOW (2); lokální aplikace, přihlášení C01–C05 nevyžaduje.
- strategy: ACCEPT
- plan: žádné opatření pro CP2.
- evidence: 2026-11-02 trigger nastal (přibyl HTTP klient, R-04) a riziko je přehodnocené: API poslouchá ve výchozím stavu jen na `127.0.0.1` a umí jen vytvořit `DRAFT` a přečíst rezervaci podle id; approve, reject ani cancel přes HTTP nejdou. Nová expozice je jen čtení cizí rezervace (vč. `user_id`) pro toho, kdo zná její id.
- expected: LOW (2)
- actual: LOW (2), přijato i po přehodnocení (L=1, I=2).
- acceptance: tým; důvod: CP2 nepožaduje přihlášení, rozhodující operace zůstávají v GUI.
- monitor: trigger – přihlášení, HTTP operace měnící stav (approve, cancel), API dostupné mimo `127.0.0.1` nebo víc uživatelů nad jednou DB.

### Issues (už nastaly, nejsou rizika)
- I-01 = C06 F2 (P1) – uzavřeno `3021f97`.
- I-02 = C06 F1 (P2) – uzavřeno `ffb9c70` + přeměření v C04 (`da3d858`).
- I-03 drobné nekonzistence dokumentace (viz C06 „Drobnosti z formuláře“) – opraveno v `da3d858`; zkontrolováno 2026-11-02 (commit v C04, 80 testů, PowerShell v README, „Proč tato úroveň“, poznámka k L1).
- I-04 měřicí skript se mohl zablokovat (připomínka z review C07) – opraveno `322bc58`.

### Přezkum rizik v C08–C10 (2026-11-16)

- **R-01:** trigger je otevřený – větev `c08-c10-ev-place` zatím nemá
  review jiného člena (C08 rozhodnutí i C09 diff) ani jeho čistý checkout.
  Proto C09 HOLD. Hodnocení LOW (2) platí jen, pokud se review a checkout
  doplní.
- **R-02:** přeměřeno po přidání `p6` (6 míst, 60 000 řádků, jeden běh na
  `21bbb36`): QC-2 1/400 při 10 000/místo, nejdelší úspěšné potvrzení
  2,77 s – v rozsahu běhů C04 při 10 000/místo (1–2/400, 1,1–5,6 s).
  Trigger D4 (víc než 1 000/místo) se netýká, objem se nezměnil. MEDIUM (4).
- **R-03:** QC-1 na Windows, na adaptéru (`BatteryStatus` 2), p95 41,2 ms;
  trigger nenastal. LOW (2).
- **R-04:** HTTP přibylo jen ošetření chyb úložiště (503), žádná nová
  operace. LOW (2).
- **R-05:** beze změny; EV jen pro majitele elektromobilů je DEFER právě
  kvůli chybějícímu přihlášení. LOW (2).
- **Nové issue I-05** (C10, už nastalo): chyba při otevření DB → HTTP bez
  odpovědi, log bez času; opraveno `0398941`.

## C08 — Řízená změna: baseline a testy před změnou

Change Card CR-08 (EV nabíjecí místo, max 4 h), Impact Matrix, varianty a
rozhodnutí: `docs/intent-and-change.md`, sekce „C08 / CR-08“.

### Baseline (krok 0)

| Položka | Hodnota |
|---|---|
| commit | `743fb2a` (`main`, merge `c07-open-items`); práce na větvi `c08-c10-ev-place` |
| specifikace | `docs/specification.md` v0.2: BR-01..BR-05, REQ-01..REQ-08; BR-02 = žádné dvě překrývající se `CONFIRMED` téhož místa |
| architektura | ADR-04 (stavové změny jen v `ReservationService`, jedna transakce), D3 (UTC ISO-8601), D4 (zámek, bez indexu) |
| schéma DB | tabulka `reservation` z C01 (`repository.py`, `_SCHEMA`), bez čísla verze a bez migrací; místa nejsou v DB, ale v kódu (`places.py`, `p1`..`p5`) |
| runtime | Windows 11 IoT Enterprise LTSC 10.0.26100, Python 3.14.3, SQLite 3.50.4; jen standardní knihovna (D1), žádné závislosti k instalaci |
| testovací data | každý test si vytvoří vlastní soubor SQLite v dočasném adresáři; žádná sdílená DB, žádná hesla ani tajné proměnné |
| příkaz | `$env:PYTHONPATH = "src"; python -m unittest discover -s tests` → na `743fb2a`: `Ran 88 tests — OK` (2026-11-16) |

### TC-C08-01 — navazující rezervace lze potvrdit

- **Test basis:** BR-01 (`[start, end)`, navazující se nepřekrývají), BR-02,
  REQ-03 (spec v0.2, příklad „`DRAFT` 10:00–11:00, existuje `CONFIRMED`
  08:00–10:00 → `CONFIRMED`“). Riziko: N/A.
- **Baseline:** `743fb2a` + testovací soubor `tests/test_c08_baseline.py`
  (kód aplikace beze změny).
- **Rozhraní:** skutečný `ReservationService` nad souborem SQLite (stejná
  cesta jako tlačítko Confirm v GUI). Uložený stav čte test **vlastním novým
  spojením `sqlite3`**, ne přes službu ani repozitář.
- **Předpoklady a data:** pevné hodiny `now` = 2026-12-01 06:00 UTC; místo
  `p1` (A-01, bez schválení); B1 `CONFIRMED` [10:00, 11:00); B2 `DRAFT`
  [11:00, 12:00); uživatelé `tc-driver-1`, `tc-driver-2` (žádné osobní údaje).
- **Příkaz:** `$env:PYTHONPATH = "src"; python -m unittest tests.test_c08_baseline -v`

| # | Akce | Expected (před během) | Actual (po běhu) |
|---|---|---|---|
| 1 | nový soubor DB, vytvořit a potvrdit B1, vytvořit B2 | jen B1 `CONFIRMED`, B2 `DRAFT` | B1 `CONFIRMED`, B2 `DRAFT` |
| 2 | nezávisle přečíst intervaly a stavy | B1 končí 11:00, B2 začíná 11:00; žádná jiná `CONFIRMED` | `[('…T10:00…', '…T11:00…', 'CONFIRMED'), ('…T11:00…', '…T12:00…', 'DRAFT')]` |
| 3 | `service.confirm(B2)` | vráceno `CONFIRMED` |
| 4 | nové spojení, přečíst uložený stav | B1 i B2 `CONFIRMED`, intervaly [10,11) a [11,12) |
| 5 | celé znovu z nového souboru | běh 2 dal totožné řádky i výsledek |

### TC-C08-02 — překrývající se potvrzení je odmítnuto, nic se nezmění

- **Test basis:** BR-02, REQ-03 (chybový výsledek „kolize s `CONFIRMED`
  rezervací → zůstává `DRAFT`“). Riziko: N/A (souběh pokrývá zvlášť REQ-04
  v `test_concurrency.py`).
- **Baseline, rozhraní, hodiny, příkaz:** jako TC-C08-01.
- **Data:** B1 `CONFIRMED` [10:00, 11:00); B2 `DRAFT` [10:30, 11:30) na `p1`.
- **Kontrakt odmítnutí:** služba vyhodí `RuleViolation`, hláška jmenuje
  kolidující rezervaci (GUI ji ukáže jako chybu). HTTP pro Confirm zatím
  neexistuje.

| # | Akce | Expected (před během) | Actual (po běhu) |
|---|---|---|---|
| 1 | nový soubor DB, B1 `CONFIRMED`, B2 `DRAFT` | B1 `CONFIRMED`, B2 `DRAFT` |
| 2 | nezávisle přečíst výchozí stav | `[('…T10:00…', '…T11:00…', 'CONFIRMED'), ('…T10:30…', '…T11:30…', 'DRAFT')]` |
| 3 | `service.confirm(B2)` | `RuleViolation: confirmed reservations for the same place must not overlap; conflicts with <id B1>` |
| 4 | nové spojení, přečíst uložený stav | totožný s krokem 2 (porovnání celých řádků) |
| 5 | celé znovu z nového souboru | běh 2 dal totožné řádky i výsledek |

**Co testy nepokrývají:** souběžné Confirm (REQ-04, zvlášť v
`test_concurrency.py`), Approve na místě se schválením, GUI samotné.

### Provedení (2026-11-16 11:50 UTC)

Kód aplikace = `743fb2a` (`git diff 743fb2a -- src` je prázdný), testy z
`cbe35ec` (Expected zapsané a commitnuté před během).

```
python -m unittest tests.test_c08_baseline -v
test_tc_c08_01_adjacent_reservation_can_be_confirmed ... ok
test_tc_c08_02_overlapping_confirm_is_rejected_and_nothing_changes ... ok
Ran 2 tests in 0.077s — OK
python -m unittest discover -s tests  ->  Ran 90 tests — OK (88 + 2)
```

Řádky v tabulkách výše jsou výpis nezávislého čtení souboru (časy zkrácené
na `…T10:00…`, plně `2026-12-01T10:00:00+00:00`).

| Test | Verdikt | Pozorovaný základ |
|---|---|---|
| TC-C08-01 | **PASS** | oba běhy: B2 `CONFIRMED`, v souboru dvě navazující `CONFIRMED` |
| TC-C08-02 | **PASS** | oba běhy: `RuleViolation` s id B1, uložené řádky beze změny |

**Umí test selhat?** Mutace v kopii kódu (z `domain.confirm` odstraněno
`_require_no_conflict`) → TC-C08-02 `FAIL: RuleViolation not raised` v obou
bězích. Test tedy BR-02 při Confirm skutečně hlídá.

**Limit důkazu:** jde o integraci služba → SQLite soubor ve stejném procesu;
GUI ani HTTP (Confirm přes HTTP neexistuje) se neprocházely. Souběh tu
není, ten ověřuje `test_concurrency.py`.

## C09 — Integration, CI and Release Record (CR-08)

**C08 Change Card a rozhodnutí:** `docs/intent-and-change.md`, „C08 / CR-08“
– ACCEPT varianty A (limit délky jako vlastnost místa), navrženo vlastníkem
R. Gřegořem 2026-11-16. **Peer review rozhodnutí zatím chybí** (viz níže),
proto implementace postupuje s vědomím, že review ji může vrátit.

**Before / after:** before = `743fb2a`. Kód CR-08 = `1969157`, automatizace
= `21bbb36`, oprava z C10 = `0398941`, test GUI pro AC-06 a aktualizace
návrhových pohledů = `e30cefe` (= **release candidate**; pozdější commity
mění jen dokumentaci). Větev `c08-c10-ev-place`, zatím nepushnutá, bez tagu.

**Dotčená tvrzení:** nové AC-01..AC-06 (BR-06, REQ-01 v0.3). Regresní báze:
TC-C08-01/02, BR-04 na ostatních místech, BR-02 na EV místě, všech 88
testů z baseline.

**Změněno:** `domain.py` (`ParkingPlace.max_duration`, `EV_MAX_DURATION`,
`create_reservation` bere limit místa), `places.py` (`p6` / `EV-01`),
`gui.py` (jen nápověda), `specification.md` (BR-06, REQ-01 v0.3), návrhové
pohledy C1/H2/DD-3 (`max_duration`), nové testy `test_cr08_ev_place.py`.

**Druhá změna ve stejném RC – oprava z C10 (I-05, `0398941`), mimo CR-08:**
`repository.py` (otevření DB hlásí `StoreBusy` nebo nový `StoreUnavailable`
místo surové `sqlite3` chyby), `service.py` (export `StoreUnavailable`),
`http_api.py` (POST i GET odpoví na tyto chyby **503** a zapíší záznam
WARNING/ERROR; dřív spojení spadlo bez odpovědi), `run_api.py` (log v UTC
s úrovní), testy `test_c10_operations.py`. HTTP kontrakt se tím **mění** o
odpověď 503 pro chyby úložiště; pro ostatní odpovědi beze změny. Důvod a
důkaz: C10 níže.

**Záměrně beze změny:** schéma DB, stavy a přechody, Confirm/Approve/Cancel,
HTTP odpovědi 201/400/404/422, ADR-04. **N/A:** migrace
(místa nejsou v DB a sloupec nepřibyl), konfigurace (limit je konstanta jako
BR-04/BR-05, aplikace nemá konfigurační soubor), worker/import (neexistují).

**Peer review:** *PENDING* – recenzent (jiný člen týmu než autor), datum,
zpochybněný předpoklad a výsledek. K prověření: hranice 4 h včetně, kontrola
jen při Create, hláška chyby, omezení rollbacku níže, oprava z C10
(`0398941`, nová odpověď 503). Bez review nelze vydat READY.

| Test ID | Nezávislý oracle | Příkaz / data | Expected | Actual | Verdikt | Evidence |
|---|---|---|---|---|---|---|
| TC-CR08-01 (AC-01, AC-02, AC-03, AC-06) | Change Card AC, spec BR-06 | `tests/test_cr08_ev_place.py::EvPlaceRule`, `now` = 06:00 | 2 h a přesně 4 h `DRAFT`; 4 h + 1 s a 4 h 1 min zamítnuto s „4 hours“ / „EV-01“; EV-01 v katalogu, 4 h, bez schválení | odpovídá | PASS | `check.py` log níže |
| TC-CR08-02 (AC-06, GUI) | AC-06 | `EvPlaceInGui`: skutečné okno `App` nad souborem SQLite s rezervací `p6` | combobox míst obsahuje `EV-01`; tabulka ukáže řádek `EV-01`, `DRAFT` | odpovídá (tkinter s displejem, nepřeskočeno) | PASS | `check.py` log |
| TC-CR08-03 (AC-02, AC-03 přes HTTP) | AC + nezávislé čtení souboru | `EvPlaceOverHttp`, skutečný HTTP server, soubor SQLite | 4 h → 201 `DRAFT` a v DB jeden řádek `p6 DRAFT`; 4 h + 1 s, 4 h 1 min → 422 „4 hours“, v DB dál jeden řádek | odpovídá | PASS | `check.py` log |
| AC-04 regrese BR-04 | spec BR-04 | `test_ac04_other_places_keep_24_hours` | A-01 24 h `DRAFT`, 25 h zamítnuto; všechna místa kromě p6 mají 24 h | odpovídá | PASS | `check.py` log |
| AC-05 BR-02 na EV-01 | BR-02 + nezávislé čtení | `EvPlaceOverlap`, soubor SQLite | překryv odmítnut, navazující potvrzena; v DB `CONFIRMED, DRAFT, CONFIRMED` | odpovídá | PASS | `check.py` log |
| C08 regrese TC-C08-01 | BR-01, BR-02, REQ-03 | `tests/test_c08_baseline.py` | jako v C08 | odpovídá | PASS | `check.py` log |
| C08 regrese TC-C08-02 | BR-02, REQ-03 | `tests/test_c08_baseline.py` | jako v C08 | odpovídá | PASS | `check.py` log |
| C10 oprava (I-05) | C10 pokus 3a/3b, D4 | `tests/test_c10_operations.py` (4) + `c10_operations.py` běh 2 | zamčeno → `StoreBusy`/503, nedostupné → `StoreUnavailable`/503 + ERROR | odpovídá | PASS | C10 níže |
| celá sada | dosavadní tvrzení C02–C07 | `python check.py` | 0 selhání | 103 testů, 0 selhání, 0 přeskočeno | PASS | níže |
| QC-1 / QC-2 po změně (Impact „měření“) | C04: p95 ≤ 100 ms; D4 | `confirm_latency.py --history 10000 --runs 200` na `21bbb36` (kód CR-08), na adaptéru, jeden běh | QC-1 dál splněno; QC-2 v rozsahu C04 | QC-1 p95 41,24 ms (60 000 řádků = 6 míst); QC-2 1/400, nejdelší úspěšné potvrzení 2,77 s | PASS | výpis níže |
| Python 3.12 (README „3.12+“) | README | – | – | – | NOT RUN | lokálně jen 3.11 a 3.14; pokryje CI matrix |

**Umí nové testy selhat?** Na kódu `743fb2a` se `test_cr08_ev_place.py`
nenačte (`parking place does not exist` pro `p6`). Mutace (globální limit
24 h místo limitu místa) → 3 FAIL (4 h + 1 s, 4 h 1 min, HTTP 422).

Výpis měření (2026-11-16, Windows na adaptéru, `BatteryStatus` 2):

```
python measurements/confirm_latency.py --history 10000 --runs 200
history/place  rows    solo p50   solo p95   solo max  2-proc p95  2-proc max  lock errors  confirmed
        10000   60000   38.92 ms   41.24 ms   43.62 ms    43.89 ms  2770.21 ms        1/400  599/600
```

V C04 při 10 000/místo (5 míst) bylo 1–2/400 chyb a nejdelší úspěšné
potvrzení 1,1–5,6 s; tento jeden běh je v tom rozsahu. Šestým místem se
QC-1 zjevně nezhoršil; jeden běh ale nestačí na tvrzení o rozptylu.

**Skutečná integrační hranice:** HTTP server na skutečném socketu →
`ReservationService` → soubor SQLite; stav čte test vlastním spojením
`sqlite3` (TC-CR08-03, AC-05, TC-C08-01/02). Je to součást `check.py`, ne
ruční krok.

**Automatizace:** `python check.py [--log check.log]` (`21bbb36`): zapíše
commit, Python, SQLite, OS; byte-compile `src/`, `tests/`, `measurements/` a
skriptů; spustí všechny testy; vypíše přeskočené; exit 0 jen při úspěchu.
Ověřeno, že selhání testu dá `result: FAIL (exit 1)` a exit kód 1.
CI: `.github/workflows/check.yml` spouští totéž na Ubuntu a Windows
s Pythonem 3.12 a 3.14 – **PLANNED / NOT RUN**, dokud větev není pushnutá.

```
commit:  e30cefe278ee9ca764e9294f9ba7c87c609eaecf
python:  3.14.3 (C:\Python314\python.exe)
sqlite:  3.50.4
os:      Windows-11-10.0.26100-SP0
build:   OK
tests run: 103, failures: 0, errors: 0, skipped: 0
result:  PASS (exit 0)
```

**Čistý checkout (stejný autor, stejný stroj – není nezávislá
reprodukce):** `git clone -c core.autocrlf=false` lokálního repozitáře do
nového adresáře (větev není na GitHubu, README uvádí URL GitHubu),
`git checkout e30cefe`, bez `.env` a bez lokálních souborů, `python check.py
--log check.log` → PASS, 103 testů; smoke podle README: `run_api.py
smoke.db` + POST `p6` 4 h → 201 `DRAFT`, GET → 200, POST `p6` 5 h → 422
„parking place EV-01 can be reserved for at most 4 hours“, v DB jeden řádek
`('p6', 'DRAFT')`. Totéž předtím na `21bbb36` (98 testů) a `0398941`
(102 testů).

**Nezávislá reprodukce jiným členem:** *PENDING (NOT RUN)*. Další krok:
push větve, jiný člen naklonuje `e30cefe` z GitHubu podle README na svém
stroji, spustí `check.py` a smoke, zapíše OS/Python, příkazy a výsledek a
porovná tree id níže.

**Identita release candidate:**

| Položka | Hodnota |
|---|---|
| zdroj | commit `e30cefe278ee9ca764e9294f9ba7c87c609eaecf`, tree `2a6f62515f791be34f2a41d8994a3f4f27d2ff6b` (identita obsahu, nezávislá na stroji); tag `c09-rc1` až po review |
| artefakt | žádný build kromě zdrojů; `git -c core.autocrlf=false archive --format=tar e30cefe` → SHA-256 `2a6de003a5d91c2053257bb475858b1e840e61b00e5e4b698513dff509ca1b45`. Bez `-c core.autocrlf=false` vyjde na Windows s `autocrlf=true` jiný hash (převod konců řádků), proto je primární identitou tree id |
| závislosti | žádné mimo standardní knihovnu (D1), proto žádný lock soubor |
| runtime | ověřeno Python 3.14.3, SQLite 3.50.4, Windows 11; README tvrdí 3.12+ (neověřeno lokálně) |
| schéma DB | tabulka `reservation` beze změny od C01, žádná migrace |
| konfigurace | jen argumenty příkazové řádky (cesta k DB, host, port); žádná tajemství |

**Rollback a jeho omezení (vyzkoušeno 2026-11-16):** DB nový kód vytvořil
rezervaci na `p6` a pak ji otevřel kód `743fb2a`. Výsledek: čtení jde,
Confirm → „parking place does not exist“, Cancel projde, výraz
`labels[r.place_id]` z `gui.refresh()` → `KeyError: 'p6'` (GUI samotné se
nespouštělo). GUI by tedy po rollbacku spadlo při překreslení tabulky, a to
i se **zrušenou** rezervací `p6`. Rollback kódu proto vyžaduje smazat řádky
`p6` (`DELETE FROM reservation WHERE place_id = 'p6'`, ztráta historie EV)
nebo nechat `p6` v katalogu staré verze. Automatizované to není. Schéma je
v obou verzích stejné, takže jinak je DB zpětně kompatibilní.

**Reziduální rizika:** EV-only uživatelé (DEFER); rollback viz výše;
review a nezávislá reprodukce chybí; CI neběželo.

**Release decision: HOLD** (owner R. Gřegoř, 2026-11-16). Automatické
testy, integrace přes HTTP + SQLite, měření i čistý checkout autorem prošly.
Chybí ale peer review rozhodnutí C08 i diffu a reprodukce jiným členem; obojí
je v C09 povinné. Změna na READY, až: (1) review zapsané výše bez
nevyřešených nálezů, (2) jiný člen zopakuje čistý checkout `e30cefe` s PASS,
(3) po pushi CI zelené (nebo zapsaný důvod, proč neběží). Zelené testy
nejsou povolení k nasazení.

## C10 — Provozní situace a logy

### Plán a očekávání (zapsáno před pokusy)

**Prostředí:** lokálně, jednorázová DB `c10/data/c10.db` v dočasném
adresáři (ne `parking.db` projektu), HTTP API `run_api.py c10/data/c10.db
--port 8766`, stderr serveru (sem jde `logging`) zachycený do souboru.
Testovací uživatelé `c10-driver-1`, `c10-driver-2`, žádné osobní údaje,
žádná hesla ani tokeny (aplikace žádné nemá).

**Omezení rozhraní:** HTTP API umí jen Create a čtení podle id. Confirm má
jen GUI, které volá `ReservationService.confirm`. Pokusy 1 a 2 proto
potvrzují tímto voláním v samostatném procesu nad stejným souborem (jako
druhá instance v G4); vytvoření a čtení jde přes HTTP. Stav se vždy ověřuje
vlastním čtením souboru přes `sqlite3`, ne z logu.

| # | Situace | Postup | Expected (před během) |
|---|---|---|---|
| 1 | Úspěšné potvrzení | POST `p1` [+3 h, +5 h) → `service.confirm(id)` → GET + čtení DB | 201 `DRAFT`; confirm vrátí `CONFIRMED`; GET i DB `CONFIRMED`. Log: řádky přístupu POST 201 a GET 200, notifikace `... is now CONFIRMED`. Log úspěchu navíc není potřeba. |
| 2 | Zamítnutá kolize | POST `p1` [+4 h, +6 h) → `service.confirm(id)` → čtení DB | 201 `DRAFT`; confirm `RuleViolation` s id rezervace z pokusu 1; DB: první `CONFIRMED`, druhá `DRAFT`. V logu nic o zamítnutí. |
| 3a | Výpadek úložiště | za běhu API přejmenovat adresář `data` → POST → adresář vrátit → POST | během výpadku: chybová odpověď serveru, nic uloženo, server běží dál; v logu čas, operace a typ chyby. Po obnovení 201 a DB obsahuje řádky z 1–2 + nový. |
| 3b | DB zamčená jiným procesem | jiný proces drží `BEGIN EXCLUSIVE` 8 s → POST | po ~5 s (`LOCK_TIMEOUT`) 503 + `Retry-After: 1`, nic uloženo; po uvolnění zámku POST 201. V logu stav 503. |
| 4 | Restart aplikace | ukončit proces API (tvrdé ukončení) → znovu spustit nad stejnou DB → GET rezervace z 1 → POST | po startu GET `CONFIRMED` beze změny; nový POST 201. V logu dvakrát řádek startu `HTTP API on ...`; zastavení v logu vidět nebude (tvrdé ukončení). |

Pokud v pokusu 3 chybí čas, operace nebo typ chyby, doplníme jeden
minimální logovací záznam a pokus zopakujeme (zadání C10).

### Provedení

Skript `measurements/c10_operations.py` (`b176e39`) pouští všechny pokusy
proti skutečnému procesu `run_api.py`. Běh 1 (2026-11-16 11:56 UTC) na
kódu `b176e39` (aplikace = `21bbb36`), běh 2 (11:58 UTC) na `0398941` po
doplnění logu a ošetření chyby. Windows 11, Python 3.14.3, SQLite 3.50.4.

```
python measurements/c10_operations.py --workdir <nový prázdný adresář>
```

| Situace | Očekávaný a skutečný výsledek | Jak jsme správnost ověřili | Co ukazují logy / je log potřeba? |
|---|---|---|---|
| Úspěšné potvrzení | **E:** 201 `DRAFT`, confirm `CONFIRMED`. **A (oba běhy):** POST 201 `DRAFT`, confirm vrátil `CONFIRMED`, GET 200 `CONFIRMED`. **PASS** | nezávislé čtení souboru: jediný řádek `c10-driver-1 14:00–16:00 CONFIRMED` | řádky přístupu `POST … 201`, `GET … 200` a `parking.notification: notify c10-driver-1: … is now CONFIRMED`. Víc není potřeba; o správnosti rozhodla odpověď a stav DB, ne log. |
| Zamítnutá kolize | **E:** `RuleViolation` s id první rezervace, nic se nezmění. **A (oba běhy):** POST 201 `DRAFT`; confirm → `RuleViolation: … conflicts with <id b1>`. **PASS** | DB: b1 `CONFIRMED`, b2 15:00–17:00 `DRAFT` (beze změny) | V logu o zamítnutí nic není a **log nepřidáváme**: jde o běžné doménové rozhodnutí, ne technickou chybu, uživatel dostane důvod v odpovědi (GUI hláška) a specifikace nepožaduje audit. Logovat každou kolizi by byl šum. Pokud by vznikl požadavek na audit sporů o místa, patří do DB, ne do textového logu. |
| Technická porucha a obnova – úložiště nedostupné (3a) | **E:** chybová odpověď, nic uloženo, server běží, v logu čas, operace, typ chyby. **A běh 1: FAIL** – žádná HTTP odpověď (`RemoteDisconnected`), v logu jen traceback socketserveru bez času a bez úrovně (`sqlite3.OperationalError: unable to open database file` v `ReservationRepository.__init__`). Server běžel dál; po vrácení adresáře POST 201. **A běh 2: PASS** – 503 `reservation store unavailable` za 0,00 s; po obnovení 201. | DB po obnovení: řádky z pokusů 1–2 + jeden nový; neúspěšný pokus nic nezapsal | Běh 1: z logu šlo poznat jen „něco spadlo při otevření DB“, ne kdy. **Doplněno** (`0398941`): jeden záznam `ERROR … POST /reservations failed: cannot open the reservation store: unable to open database file (database …)` a časy v UTC se `Z` a úrovní. |
| Technická porucha – DB zamčená jiným procesem (3b) | **E:** po ~5 s 503 + `Retry-After: 1`, nic uloženo. **A běh 1: FAIL** – po 7,51 s žádná odpověď; příčina `database is locked` v `CREATE TABLE IF NOT EXISTS` při otevření repozitáře, tedy mimo `transaction()`, takže tvrzení D4 „StoreBusy, nic se nezměnilo“ pro otevření neplatilo. **A běh 2: PASS** – 503 `the reservation store is busy, try again`, `Retry-After: 1`, za 7,49 s; po uvolnění zámku 201. | DB po uvolnění: předchozí řádky + jeden nový; zamítnutý pokus nic nezapsal | běh 2: `WARNING … POST /reservations: store busy, nothing changed (database is locked)` + řádek přístupu 503. Čekání 7,5 s místo `LOCK_TIMEOUT` 5 s je pozorované, příčina neověřená (pravděpodobně čekání SQLite při otevření a schématu). |
| Restart aplikace | **E:** po startu GET `CONFIRMED` beze změny, nový POST 201; v logu dva starty, zastavení ne. **A (oba běhy):** proces ukončen tvrdě (`terminate`, exit 1), znovu spuštěn nad stejnou DB za 0,5 s; GET b1 → `CONFIRMED`; POST → 201. **PASS** | DB po restartu: všechny dřívější řádky + nový, b1 dál `CONFIRMED` | dva řádky `HTTP API on http://127.0.0.1:8766, database …`; zastavení v logu **není** (tvrdé ukončení nic nezapíše). Výpadek je v logu vidět jen jako mezera mezi posledním požadavkem a novým startem. Řádné zastavení Ctrl+C jsme nezkoušeli (**NOT RUN**: v neinteraktivním prostředí nejde poslat Ctrl+C); log zastavení nepřidáváme, protože pád ho stejně nezapíše a start s cestou k DB stačí. |

Výřez logu, běh 1 (před doplněním), pokus 3a:

```
2026-11-16 12:56:07,385 parking.http: 127.0.0.1 "POST /reservations HTTP/1.1" 201 -
----------------------------------------
Exception occurred during processing of request from ('127.0.0.1', 65187)
Traceback (most recent call last):
  ...
  File "...\src\parking\repository.py", line 31, in __init__
    self._db = sqlite3.connect(path, isolation_level=None, timeout=lock_timeout)
sqlite3.OperationalError: unable to open database file
----------------------------------------
```

(čas `12:56` je místní čas bez offsetu; DB pracuje v UTC, tj. 11:56)

Výřez logu, běh 2 (po doplnění), pokusy 3a, 3b a 4:

```
2026-11-16T11:58:13.564Z ERROR parking.http: POST /reservations failed: cannot open the reservation store: unable to open database file (database …\data\c10.db)
2026-11-16T11:58:13.564Z INFO parking.http: 127.0.0.1 "POST /reservations HTTP/1.1" 503 -
2026-11-16T11:58:13.585Z INFO parking.http: 127.0.0.1 "POST /reservations HTTP/1.1" 201 -
2026-11-16T11:58:21.136Z WARNING parking.http: POST /reservations: store busy, nothing changed (database is locked)
2026-11-16T11:58:21.136Z INFO parking.http: 127.0.0.1 "POST /reservations HTTP/1.1" 503 -
2026-11-16T11:58:21.679Z INFO parking.http: 127.0.0.1 "POST /reservations HTTP/1.1" 201 -
2026-11-16T11:58:21.911Z INFO parking.http: HTTP API on http://127.0.0.1:8766, database …\data\c10.db
2026-11-16T11:58:22.202Z INFO parking.http: 127.0.0.1 "GET /reservations/32821bea… HTTP/1.1" 200 -
```

**Změna kódu z C10** (`0398941`, minimální): otevření repozitáře hlásí
`StoreBusy` (zamčeno) nebo nový `StoreUnavailable` (soubor nejde otevřít)
místo surové `sqlite3` chyby; HTTP API odpoví 503 a zapíše jeden záznam
s operací a typem chyby; `run_api.py` loguje v UTC s úrovní. Klienti dál
neimportují `sqlite3` (architektonický test). Testy
`tests/test_c10_operations.py` (4): zámek při otevření → `StoreBusy`,
chybějící adresář → `StoreUnavailable`, HTTP POST/GET → 503 + záznam ERROR.

**Závěr.**
- *Co má smysl logovat:* start (adresa a cesta k DB), technické chyby
  úložiště s operací a typem (ERROR) a odmítnutí kvůli zámku (WARNING, nic
  se nezměnilo). Řádky přístupu stačí pro časovou osu. Úspěchy a doménová
  zamítnutí navíc ne.
- *Co jsme z logů skutečně zjistili:* kdy a ve které operaci selhalo
  otevření DB (po doplnění), že server po chybě běžel dál, kdy byl znovu
  spuštěn a nad jakou DB. Před doplněním z logu nešlo zjistit čas ani to,
  že klient nedostal odpověď.
- *Co jsme museli ověřit jinak:* že potvrzení skutečně platí, že kolize
  nic nezměnila, že neúspěšné pokusy nic nezapsaly a že po restartu
  `CONFIRMED` zůstala – vše nezávislým čtením souboru přes `sqlite3`.
  Odpověď klienta při výpadku (žádná vs. 503) ukázal až klient, ne log.
  Okamžik tvrdého ukončení z logu nezjistíme.
- *Zbývá:* `LogNotifier` zapisuje `user_id` (zástupce e-mailové
  notifikace). U testovacích id to nevadí, v provozu by to byl osobní údaj
  v logu a musí se nahradit skutečnou notifikací nebo pseudonymem. GUI
  (`run.py`) loguje dál v místním čase; v C10 se netestovalo. Confirm přes
  HTTP neexistuje, pokusy 1–2 proto šly přes službu v samostatném procesu.
  Ošetřené je jen *otevření* DB: čtení mimo `transaction()` (`get`,
  `for_place`, `all`, např. GUI `refresh()` nebo GET po otevření) by při
  zámku, který vznikne mezi otevřením a čtením, dál skončilo surovou
  `sqlite3` chybou; nevyzkoušeno.
- *Doplňující pokus 3b s delším zámkem* (`e30cefe`, 12:04 UTC): zámek
  držený 20 s → 503 po 7,50 s (čekání je omezené, nekopíruje délku zámku);
  zámek 2 s → POST počká 2,1 s a uspěje 201. Mez je tedy ~7,5 s, ne 5 s
  z `LOCK_TIMEOUT`; příčina neověřená.
