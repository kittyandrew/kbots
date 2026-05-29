import datetime
import logging
from contextlib import nullcontext
from typing import List, Literal, Optional

import sentry_sdk
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from .prompts import VEHICLE_EXPORT_EXTRA, VEHICLE_EXPORT_SYSTEM, VEHICLE_EXPORT_USER

logger = logging.getLogger(__name__)


class Item(BaseModel):
    name: str = Field(
        description="Full english vehicle name, including type (T72M1 MBT, M113 APC etc), with any quotes properly escaped."
    )
    ownership: Optional[Literal["ru", "ua"]] = Field(
        description="Who equipment belonged to originally (required, unless neither is specified)"
    )
    status: Literal["damaged", "destroyed", "captured"] = Field(
        description="Status of the equipment (damaged if it was left behind)"
    )
    post_date: Optional[str] = Field(
        description="If provided, the format is day.month.year (e.g. 20.4.2024) or season name with year, defaulting to the first date of certain month (rules: winter -> 01.01, spring -> 01.03, summer -> 01.06, fall -> 01.09). If only the year was provided, assume first date of december."
    )


class Vehicles(BaseModel):
    vehicles: List[Item] = Field(description="List of vehicle objects that were detected")


async def parse_messages(texts: list[str], extra_prompt: str, sem=None) -> list[Item]:
    async with sem or nullcontext():
        # @NOTE: Keep this up to date with the latest cost-efficient model.
        llm = ChatOpenAI(model="gpt-5-mini").with_structured_output(Vehicles)
        date_now = datetime.datetime.now().strftime("%B %d, %Y")

        system_extra = VEHICLE_EXPORT_EXTRA.format(extra=extra_prompt, date=date_now)
        # @NOTE: Using % formatting instead of str.format() — originally changed to avoid potential
        # KeyError from braces in Telegram text, but str.format() only interprets braces in the
        # template, not in substituted values, so that reasoning was incorrect. Kept as %s since
        # it's simpler for single-substitution templates and works fine.
        user_message = VEHICLE_EXPORT_USER % "\n\n".join([f"<message>\n{t}\n</message>" for t in texts])

        sentry_sdk.add_breadcrumb(category="llm", message=f"Parsing {len(texts)} messages via OpenAI")
        try:
            res = await llm.ainvoke([SystemMessage(VEHICLE_EXPORT_SYSTEM + system_extra), HumanMessage(user_message)])
            assert isinstance(res, Vehicles), f"Broken structured output? ({res} of type {type(res)})"
            return res.vehicles
        except Exception:
            logger.exception("Error parsing messages")
            return []
