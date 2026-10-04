class DomainError(Exception):
    """Ожидаемая ошибка бизнес-логики; текст показывается пользователю."""


class PermissionDenied(DomainError):
    pass


class NotFound(DomainError):
    pass


class Conflict(DomainError):
    pass
