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

**Commit / tag aplikace:** větev `c02-specification`, aplikace odpovídající
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
