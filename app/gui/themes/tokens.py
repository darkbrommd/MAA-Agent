from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from gui.resource_paths import resource_path


@dataclass(frozen=True)
class ThemeTokens:
    name: str
    background: str
    navigation: str
    card: str
    border: str
    text: str
    muted: str
    accent: str
    success: str
    warning: str
    danger: str
    failure_card: str
    running_card: str
    next_card: str
    future_card: str
    ended_card: str
    ended: str
    off_card: str
    off: str
    disabled_text: str
    disabled_background: str

    def mapping(self) -> dict[str, str]:
        values = asdict(self)
        values["combo_arrow"] = resource_path("assets", "icons", "chevron-down.svg").as_posix()
        return values


DARK = ThemeTokens(
    name="dark",
    background="#11151C",
    navigation="#181D26",
    card="#1C222D",
    border="#2A3241",
    text="#E6EDF3",
    muted="#8B949E",
    accent="#6399EE",
    success="#38B880",
    warning="#E3B341",
    danger="#EF6868",
    failure_card="#3A1E22",
    running_card="#3B3420",
    next_card="#203A2A",
    future_card="#222D3D",
    ended_card="#252A31",
    ended="#748092",
    off_card="#14181F",
    off="#566172",
    disabled_text="#5C6573",
    disabled_background="#161B22",
)

LIGHT = ThemeTokens(
    name="light",
    background="#F4F5F7",
    navigation="#EBEEF2",
    card="#FFFFFF",
    border="#D8DEE6",
    text="#1D2430",
    muted="#5A6577",
    accent="#3976CD",
    success="#16865B",
    warning="#C58308",
    danger="#D64545",
    failure_card="#FCEBEB",
    running_card="#FFF1C2",
    next_card="#DFF3E5",
    future_card="#FFFFFF",
    ended_card="#E3E8ED",
    ended="#8C96A5",
    off_card="#EEF1F5",
    off="#9AA4B2",
    disabled_text="#9AA4B2",
    disabled_background="#EEF1F5",
)


def load_stylesheet(theme: str) -> str:
    tokens = DARK if theme == "dark" else LIGHT
    path = Path(__file__).with_name(f"{tokens.name}.qss")
    template = path.read_text(encoding="utf-8")
    return template.format_map(tokens.mapping())
