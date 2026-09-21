"""Minimal tkinter GUI for the parking reservation system."""
import tkinter as tk
from datetime import datetime, timedelta, timezone
from tkinter import messagebox, ttk

from parking.domain import (
    ParkingPlace, RuleViolation, State, User,
    approve, cancel, confirm, create_reservation, expire_if_due, is_available, reject,
)
from parking.repository import ReservationRepository

PLACES = [ParkingPlace(f"p{i}", f"A-{i:02d}") for i in range(1, 5)] + [
    ParkingPlace("p5", "VIP-01", requires_approval=True),
]
FMT = "%Y-%m-%d %H:%M"


def _parse(text: str) -> datetime:
    return datetime.strptime(text.strip(), FMT).replace(tzinfo=timezone.utc)


class App(tk.Tk):
    def __init__(self, repo: ReservationRepository):
        super().__init__()
        self.repo = repo
        self.title("Parking reservations")

        form = ttk.Frame(self, padding=10)
        form.grid(row=0, column=0, sticky="ew")
        self.place = tk.StringVar(value=PLACES[0].label)
        self.user = tk.StringVar()
        default_start = datetime.now() + timedelta(hours=2)
        default_end = default_start + timedelta(hours=2)
        self.start = tk.StringVar(value=default_start.strftime(FMT))
        self.end = tk.StringVar(value=default_end.strftime(FMT))
        for row, (label, widget) in enumerate([
            ("Place", ttk.Combobox(form, textvariable=self.place, values=[p.label for p in PLACES], state="readonly")),
            ("User", ttk.Entry(form, textvariable=self.user)),
            ("Start (UTC)", ttk.Entry(form, textvariable=self.start)),
            ("End (UTC)", ttk.Entry(form, textvariable=self.end)),
        ]):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=2)
            widget.grid(row=row, column=1, sticky="ew", pady=2)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Places named VIP-* require Facility manager approval before Confirm.",
                  foreground="#555").grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(form, text="Start must be at least 1 hour from now — no reservations in the past.",
                  foreground="#555").grid(row=5, column=0, columnspan=2, sticky="w")

        buttons = ttk.Frame(self, padding=(10, 0))
        buttons.grid(row=1, column=0, sticky="ew")
        for text, cmd in [
            ("Check availability", self.check), ("Create", self.create),
            ("Confirm", self.confirm), ("Approve", self.approve),
            ("Reject", self.reject), ("Cancel", self.cancel),
        ]:
            ttk.Button(buttons, text=text, command=cmd).pack(side="left", padx=2)

        cols = ("id", "place", "user", "start", "end", "state")
        self.table = ttk.Treeview(self, columns=cols, show="headings", height=10)
        for c in cols:
            self.table.heading(c, text=c.capitalize())
            self.table.column(c, width=90 if c != "id" else 120)
        self.table.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.refresh()

    # --- helpers -------------------------------------------------------
    def _place(self) -> ParkingPlace:
        return next(p for p in PLACES if p.label == self.place.get())

    def _place_of(self, r) -> ParkingPlace:
        return next(p for p in PLACES if p.id == r.place_id)

    def _selected_id(self) -> str | None:
        sel = self.table.selection()
        if not sel:
            messagebox.showinfo("Nothing selected", "Select a reservation in the table first.")
            return None
        return sel[0]

    def _guard(self, action):
        try:
            action()
        except (RuleViolation, ValueError) as e:
            messagebox.showerror("Not allowed", str(e))
        self.refresh()

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def refresh(self):
        # A PENDING_APPROVAL request nobody decided on before its own start
        # time is no longer actionable (see OP-05 / expire_if_due).
        now = self._now()
        for p in PLACES:
            for r in self.repo.for_place(p.id):
                if r.state == State.PENDING_APPROVAL and now >= r.start:
                    self.repo.apply(r.id, lambda r, _: expire_if_due(r, now))

        self.table.delete(*self.table.get_children())
        labels = {p.id: p.label for p in PLACES}
        for p in PLACES:
            for r in sorted(self.repo.for_place(p.id), key=lambda r: r.start):
                self.table.insert("", "end", iid=r.id, values=(
                    r.id[:8], labels[r.place_id], r.user_id,
                    r.start.strftime(FMT), r.end.strftime(FMT), r.state.value))

    # --- actions -------------------------------------------------------
    def check(self):
        def go():
            place = self._place()
            free = is_available(place.id, _parse(self.start.get()), _parse(self.end.get()), self.repo.for_place(place.id))
            messagebox.showinfo("Availability", f"{place.label} is {'available' if free else 'NOT available'}.")
        self._guard(go)

    def create(self):
        def go():
            if not self.user.get().strip():
                raise ValueError("user is required")
            user = User(self.user.get().strip(), self.user.get().strip())
            self.repo.save(create_reservation(
                self._place(), user, _parse(self.start.get()), _parse(self.end.get()), self._now()))
        self._guard(go)

    # Confirm, Approve, Reject and Cancel go through repo.apply, which checks
    # the rule and writes the result atomically (REQ-04).
    def confirm(self):
        def go():
            if rid := self._selected_id():
                self.repo.apply(rid, lambda r, existing: confirm(r, existing, self._place_of(r), self._now()))
        self._guard(go)

    def approve(self):
        def go():
            if rid := self._selected_id():
                self.repo.apply(rid, lambda r, existing: approve(r, existing, self._now()))
        self._guard(go)

    def reject(self):
        def go():
            if rid := self._selected_id():
                self.repo.apply(rid, lambda r, _: reject(r, self._now()))
        self._guard(go)

    def cancel(self):
        def go():
            if rid := self._selected_id():
                self.repo.apply(rid, lambda r, _: cancel(r, self._now()))
        self._guard(go)


def main(db_path: str = "parking.db") -> None:
    repo = ReservationRepository(db_path)
    try:
        App(repo).mainloop()
    finally:
        repo.close()
