"""Minimal tkinter GUI for the parking reservation system."""
import tkinter as tk
from datetime import datetime, timezone
from tkinter import messagebox, ttk

from parking.domain import ParkingPlace, RuleViolation, User, cancel, confirm, create_reservation, is_available
from parking.repository import ReservationRepository

PLACES = [ParkingPlace(f"p{i}", f"A-{i:02d}") for i in range(1, 6)]
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
        self.start = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d 08:00"))
        self.end = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d 10:00"))
        for row, (label, widget) in enumerate([
            ("Place", ttk.Combobox(form, textvariable=self.place, values=[p.label for p in PLACES], state="readonly")),
            ("User", ttk.Entry(form, textvariable=self.user)),
            ("Start (UTC)", ttk.Entry(form, textvariable=self.start)),
            ("End (UTC)", ttk.Entry(form, textvariable=self.end)),
        ]):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=2)
            widget.grid(row=row, column=1, sticky="ew", pady=2)
        form.columnconfigure(1, weight=1)

        buttons = ttk.Frame(self, padding=(10, 0))
        buttons.grid(row=1, column=0, sticky="ew")
        for text, cmd in [("Check availability", self.check), ("Create", self.create),
                          ("Confirm", self.confirm), ("Cancel", self.cancel)]:
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

    def _selected(self):
        sel = self.table.selection()
        if not sel:
            messagebox.showinfo("Nothing selected", "Select a reservation in the table first.")
            return None
        return self.repo.get(sel[0])

    def _guard(self, action):
        try:
            action()
        except (RuleViolation, ValueError) as e:
            messagebox.showerror("Not allowed", str(e))
        self.refresh()

    def refresh(self):
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
            self.repo.save(create_reservation(self._place(), user, _parse(self.start.get()), _parse(self.end.get())))
        self._guard(go)

    def confirm(self):
        def go():
            if r := self._selected():
                confirm(r, self.repo.for_place(r.place_id))
                self.repo.save(r)
        self._guard(go)

    def cancel(self):
        def go():
            if r := self._selected():
                cancel(r)
                self.repo.save(r)
        self._guard(go)


def main(db_path: str = "parking.db") -> None:
    repo = ReservationRepository(db_path)
    try:
        App(repo).mainloop()
    finally:
        repo.close()
