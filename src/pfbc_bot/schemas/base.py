from pydantic import BaseModel, ConfigDict


class DTO(BaseModel):
    """Неизменяемый DTO, создаётся из ORM-объектов (from_attributes)."""

    model_config = ConfigDict(from_attributes=True, frozen=True)
