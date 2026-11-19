"""The template catalogue the UI shows."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import AppState, get_state
from app.templates import TemplateField

router = APIRouter(prefix="/api")


class TemplateInfo(BaseModel):
    """A template as the UI needs it. The request text stays on the server."""

    id: str
    name: str
    description: str
    fields: list[TemplateField]
    example: dict[str, str]


@router.get("/templates")
async def list_templates(
    state: Annotated[AppState, Depends(get_state)],
) -> list[TemplateInfo]:
    return [
        TemplateInfo(
            id=t.id,
            name=t.name,
            description=t.description,
            fields=t.fields,
            example=t.example,
        )
        for t in state.templates
    ]
