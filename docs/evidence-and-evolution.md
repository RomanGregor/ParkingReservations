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
