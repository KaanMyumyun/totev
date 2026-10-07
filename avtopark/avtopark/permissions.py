"""User roles and the permissions granted to each role."""


class Role:
    ADMIN = "admin"
    MANAGER = "manager"
    MECHANIC = "mechanic"
    LOGISTICIAN = "logistician"


ROLE_LABELS = {
    Role.ADMIN: "Администратор",
    Role.MANAGER: "Мениджър",
    Role.MECHANIC: "Механик",
    Role.LOGISTICIAN: "Логистик",
}

_ALL = frozenset(ROLE_LABELS)
_OFFICE = frozenset({Role.ADMIN, Role.MANAGER})

# Every logged-in user can view vehicles, records and the maintenance check.
# The table below lists the actions that change data or show financial reports.
PERMISSIONS = {
    "vehicles.manage": _OFFICE,
    "vehicles.status": _OFFICE | {Role.MECHANIC},
    "odometer.add": _ALL,
    "maintenance.manage": _OFFICE | {Role.MECHANIC},
    "plans.manage": _OFFICE | {Role.MECHANIC},
    "types.manage": _OFFICE,
    "fuel.manage": _OFFICE | {Role.LOGISTICIAN},
    "trips.manage": _OFFICE | {Role.LOGISTICIAN},
    "drivers.manage": _OFFICE | {Role.LOGISTICIAN},
    "insurance.manage": _OFFICE,
    "reports.costs": _OFFICE,
    "records.delete": _OFFICE,
    "users.manage": frozenset({Role.ADMIN}),
    "audit.view": frozenset({Role.ADMIN}),
}


def role_can(role: str, permission: str) -> bool:
    return role in PERMISSIONS.get(permission, ())
