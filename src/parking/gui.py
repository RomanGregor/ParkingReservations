"""Minimal tkinter GUI for the parking reservation system."""
import tkinter as tk
from datetime import datetime, timedelta, timezone
from tkinter import messagebox, ttk

from parking.domain import ParkingPlace, RuleViolation, User
from parking.service import ReservationService, open_service

FMT = "%Y-%m-%d %H:%M"
EXPIRY_CHECK_MS = 60_000  # REQ-08: look for due PENDING_APPROVAL requests every minute


def _parse(text: str) -> datetime:
    return datetime.strptime(text.strip(), FMT).replace(tzinfo=timezone.utc)


class App(tk.Tk):
    def __init__(self, service: ReservationService):
        super().__init__()
        self.service = service
        self.places = service.places()
        self.title("Parking reservations")

        form = ttk.Frame(self, padding=10)
        form.grid(row=0, column=0, sticky="ew")
        self.place = tk.StringVar(value=self.places[0].label)
        self.user = tk.StringVar()
        default_start = datetime.now() + timedelta(hours=2)
        default_end = default_start + timedelta(hours=2)
        self.start = tk.StringVar(value=default_start.strftime(FMT))
        self.end = tk.StringVar(value=default_end.strftime(FMT))
        for row, (label, widget) in enumerate([
            ("Place", ttk.Combobox(form, textvariable=self.place, values=[p.label for p in self.places], state="readonly")),
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
        self._tick()

    # --- helpers -------------------------------------------------------
    def _place(self) -> ParkingPlace:
        return next(p for p in self.places if p.label == self.place.get())

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

    def _tick(self):
        # Schedule first, so one failed refresh does not stop the expiry check.
        self.after(EXPIRY_CHECK_MS, self._tick)
        self.refresh()

    def refresh(self):
        # The UI only triggers the expiry check; ReservationService decides
        # which requests expire (REQ-08, ADR-04).
        self.service.expire_due()

        self.table.delete(*self.table.get_children())
        order = {p.id: i for i, p in enumerate(self.places)}
        labels = {p.id: p.label for p in self.places}
        for r in sorted(self.service.reservations(), key=lambda r: (order[r.place_id], r.start)):
            self.table.insert("", "end", iid=r.id, values=(
                r.id[:8], labels[r.place_id], r.user_id,
                r.start.strftime(FMT), r.end.strftime(FMT), r.state.value))

    # --- actions -------------------------------------------------------
    def check(self):
        def go():
            place = self._place()
            free = self.service.check(place.id, _parse(self.start.get()), _parse(self.end.get()))
            messagebox.showinfo("Availability", f"{place.label} is {'available' if free else 'NOT available'}.")
        self._guard(go)

    def create(self):
        def go():
            if not self.user.get().strip():
                raise ValueError("user is required")
            user = User(self.user.get().strip(), self.user.get().strip())
            self.service.create(self._place().id, user, _parse(self.start.get()), _parse(self.end.get()))
        self._guard(go)

    # Confirm, Approve, Reject and Cancel only request the transition;
    # ReservationService decides it and writes it atomically (REQ-04, ADR-04).
    def confirm(self):
        def go():
            if rid := self._selected_id():
                self.service.confirm(rid)
        self._guard(go)

    def approve(self):
        def go():
            if rid := self._selected_id():
                self.service.approve(rid)
        self._guard(go)

    def reject(self):
        def go():
            if rid := self._selected_id():
                self.service.reject(rid)
        self._guard(go)

    def cancel(self):
        def go():
            if rid := self._selected_id():
                self.service.cancel(rid)
        self._guard(go)


def main(db_path: str = "parking.db") -> None:
    service = open_service(db_path)
    try:
        App(service).mainloop()
    finally:
        service.close()
