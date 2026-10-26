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

„2-proc max“ je nejdelší *úspěšné* potvrzení. Že může přesáhnout 5 s,
pravděpodobně způsobuje to, že busy handler SQLite započítává do timeoutu
součet svých plánovaných uspání, ne skutečně uplynulý čas (neověřeno).

**Výsledek** (platí od C07):

| Criterion | Naměřeno (10 000/místo) | Výsledek |
|---|---|---|
| QC-1 p95 ≤ 100 ms | Linux (C04) 77–80 ms ve 4 bězích; Windows 46–67 ms v 5 bězích ze 6, jeden běh 150 ms (max 786 ms); hodnotitelé C06 na Windows 94,7 ms | **splněno v 9 z 10 vlastních běhů**; jeden odlehlý běh, příčina neověřená. Rezerva na Windows hodnotitelů jen 5 %. Při 50 000/místo 230 ms |
| QC-2 0 chyb zámku ze 400 souběžných | 1–2/400 v každém ze 6 běhů; hodnotitelé C06 2–3/400 | **nesplněno**. Při 1 000/místo 0/400 (náš běh i hodnotitelé), při 50 000/místo 7/400 |

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

**Zbývající mezery:** měřeno na jednom stroji s Linuxem (C04) a jednom
s Windows (C07), oba lokální SSD; síťový disk měřen není. Jeden běh QC-1
na Windows (150 ms) je nevysvětlený. Historie je syntetická a objem je
předpoklad, ne údaj provozu.

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
  chyby pravidla.
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

## C07 — Risk register

Klasifikace před ošetřením: L a I 1–3, priorita L × I (1–2 LOW, 3–4
MEDIUM, 6–9 HIGH). Actual residual se snižuje jen s ověřeným opatřením,
jinak NOT VERIFIED. Stav k větvi `c07-c06-findings`.

### R-01
- statement: Implementaci a evidenci C02–C04 (`service.py`, `test_concurrency.py`, `confirm_latency.py`; 24 ze 40 commitů) napsal hlavně jeden člen → při jeho nedostupnosti nikdo jiný neopraví nálezy ani nezopakuje měření → uzavření C06 findings a příprava CP2 se zpozdí.
- category: People / Resources
- initial: L=2, I=3 → HIGH (6); zastupitelnost nebyla vyzkoušena, C05 také vytvořil jediný člen.
- strategy: MITIGATE – omezit single point of failure.
- plan: opravy F1 a F2 udělá jiný člen než autor původního kódu, s review autora; čistý checkout + testy + měření podle README na druhém stroji (Windows). Owner: R. Gřegoř.
- evidence: F1 a F2 opravil R. Gřegoř (`3021f97`, `ffb9c70`), měření proběhlo na jeho stroji s Windows. Review autorem původního kódu zatím neproběhlo.
- expected: L=1, I=2 → LOW (2)
- actual: NOT VERIFIED – chybí review opravy druhým členem.
- acceptance: po review přijme R. Gřegoř za tým.
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
- evidence: README doplněno; C04 přeměřeno na Windows (C04 výše).
- expected: L=1, I=2 → LOW (2)
- actual: MEDIUM (4) – README je opravené, ale QC-1 na Windows není stabilní: 5 z 6 běhů 46–67 ms, jeden 150 ms, hodnotitelé 94,7 ms. Trigger (> 90 ms) tím nastal; další krok je zjistit příčinu odlehlého běhu.
- acceptance: R. Gřegoř; contingency: obhajovat na stroji, kde je měření doložené.
- monitor: trigger – p95 QC-1 > 90 ms na kterémkoli vykázaném stroji (rezerva < 10 %).

### R-04
- statement: README plánuje HTTP adaptér (CP1 walking skeleton, AD-04), který neexistuje; DD-4 předpokládá vstup jako aware UTC → adaptér psaný těsně před CP2 → `TypeError` místo chyby pravidla, neověřený druhý proces nad SQLite, nehotová demonstrace.
- category: Schedule / Integration
- initial: L=2, I=2 → MEDIUM (4); adaptér nezačat, termín CP2 v plánu týmu chybí.
- strategy: AVOID (návrh) – pokud HTTP není v rozsahu CP2, zůstává GUI jediným klientem; jinak MITIGATE (tenký adaptér + 1 E2E test).
- plan: rozhodnout s cvičícím na C08. Owner: R. Gřegoř.
- evidence: zatím žádná.
- expected: AVOID: L=1, I=1 → LOW (1)
- actual: NOT VERIFIED – rozhodnutí nepadlo.
- acceptance: tým + cvičící.
- monitor: trigger – cvičící potvrdí HTTP v rozsahu CP2.

### R-05
- statement: Role se neověřují (C03 F6) a hláška kolize ukazuje id cizí rezervace → při víceuživatelském provozu schválí VIP žádost neoprávněná osoba → obejití schvalování v0.2.
- category: Security / Privacy
- initial: L=1, I=2 → LOW (2); lokální aplikace, přihlášení C01–C05 nevyžaduje.
- strategy: ACCEPT
- plan: žádné opatření pro CP2.
- evidence: nic k ověření.
- expected: LOW (2)
- actual: LOW (2), přijato.
- acceptance: tým; důvod: CP2 nepožaduje přihlášení.
- monitor: trigger – přibude přihlášení, HTTP klient nebo víc uživatelů nad jednou DB.

### Issues (už nastaly, nejsou rizika)
- I-01 = C06 F2 – uzavřeno `3021f97`.
- I-02 = C06 F1 – uzavřeno `ffb9c70` + přeměření v C04.
- I-03 drobné nekonzistence dokumentace (viz výše) – opraveno v C07.
