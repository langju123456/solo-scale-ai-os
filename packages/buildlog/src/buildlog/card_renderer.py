"""Deterministic PNG rendering for validated publishing card specs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from buildlog.exceptions import PackageBuildError
from buildlog.package_models import (
    ArchitectureCardSpec,
    AssetPlan,
    CardSpec,
    TakeawayCardSpec,
    TitleCardSpec,
    TradeoffCardSpec,
)

CARD_WIDTH = 1080
CARD_HEIGHT = 1350
TEMPLATE_VERSION = "v1"

PAPER = "#F7F8F5"
WHITE = "#FFFFFF"
INK = "#111827"
MUTED = "#5F6875"
LINE = "#D5DAE1"
BLUE = "#1769E0"
GREEN = "#0F766E"
CORAL = "#D94F28"
YELLOW = "#F1C84B"


@dataclass(frozen=True)
class RenderedCard:
    """One rendered card and the spec that produced it."""

    position: int
    path: Path
    spec: CardSpec
    alt_text: str


class CardRenderer:
    """Render consistent LinkedIn cards from structured specs."""

    width = CARD_WIDTH
    height = CARD_HEIGHT
    template_version = TEMPLATE_VERSION

    def render(self, plan: AssetPlan, assets_dir: Path) -> list[RenderedCard]:
        """Render every card in order and return their file records."""
        try:
            assets_dir.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise PackageBuildError(
                f"could not create package assets directory: {assets_dir}"
            ) from exc

        rendered: list[RenderedCard] = []
        for position, spec in enumerate(plan.cards, start=1):
            filename = f"card_{position:02d}_{spec.type}.png"
            path = assets_dir / filename
            try:
                if isinstance(spec, TitleCardSpec):
                    self._render_title(spec, position, path)
                elif isinstance(spec, ArchitectureCardSpec):
                    self._render_architecture(spec, position, path)
                elif isinstance(spec, TradeoffCardSpec):
                    self._render_tradeoff(spec, position, path)
                elif isinstance(spec, TakeawayCardSpec):
                    self._render_takeaway(spec, position, path)
                else:  # pragma: no cover - protected by the discriminated union
                    raise PackageBuildError(f"unsupported card type: {spec.type}")
            except PackageBuildError:
                raise
            except Exception as exc:
                raise PackageBuildError(f"could not render {filename}: {exc}") from exc
            rendered.append(
                RenderedCard(
                    position=position,
                    path=path,
                    spec=spec,
                    alt_text=_alt_text(spec),
                )
            )
        return rendered

    def _render_title(
        self,
        spec: TitleCardSpec,
        position: int,
        path: Path,
    ) -> None:
        image, draw = _canvas()
        draw.rectangle((0, 0, 18, CARD_HEIGHT), fill=BLUE)
        draw.rectangle((72, 82, 248, 92), fill=CORAL)
        _draw_label(draw, "BUILDLOG / ENGINEERING NOTE", 72, 128, BLUE)

        title_font, title_lines = _fit_block(
            draw,
            spec.title,
            max_width=900,
            max_height=470,
            max_size=84,
            min_size=52,
            spacing=12,
            bold=True,
        )
        draw.multiline_text(
            (72, 270),
            "\n".join(title_lines),
            font=title_font,
            fill=INK,
            spacing=12,
        )
        title_height = _lines_height(draw, title_lines, title_font, 12)
        subtitle_y = min(850, 270 + title_height + 70)
        _draw_wrapped(
            draw,
            spec.subtitle,
            (72, subtitle_y),
            max_width=870,
            max_height=260,
            max_size=38,
            min_size=28,
            color=MUTED,
            spacing=10,
        )
        draw.rectangle((72, 1164, 410, 1172), fill=YELLOW)
        _draw_footer(draw, position)
        image.save(path, format="PNG", optimize=True)

    def _render_architecture(
        self,
        spec: ArchitectureCardSpec,
        position: int,
        path: Path,
    ) -> None:
        image, draw = _canvas()
        _draw_header(draw, "ARCHITECTURE", spec.title, BLUE)

        count = len(spec.steps)
        top = 320
        box_height = 112 if count == 5 else 128
        gap = 42
        x1, x2 = 128, 952
        center_x = CARD_WIDTH // 2
        step_font_size = 28 if count == 5 else 31
        for index, step in enumerate(spec.steps):
            y1 = top + index * (box_height + gap)
            y2 = y1 + box_height
            if index:
                draw.line(
                    (center_x, y1 - gap + 8, center_x, y1 - 8),
                    fill=GREEN,
                    width=5,
                )
                draw.polygon(
                    [
                        (center_x - 10, y1 - 18),
                        (center_x + 10, y1 - 18),
                        (center_x, y1 - 6),
                    ],
                    fill=GREEN,
                )
            draw.rounded_rectangle(
                (x1, y1, x2, y2),
                radius=8,
                fill=WHITE,
                outline=LINE,
                width=2,
            )
            draw.ellipse((154, y1 + 31, 204, y1 + 81), fill=BLUE)
            number_font = _font(24, bold=True)
            _draw_centered(draw, str(index + 1), (179, y1 + 56), number_font, WHITE)
            _draw_wrapped(
                draw,
                step,
                (232, y1 + 24),
                max_width=670,
                max_height=78,
                max_size=step_font_size,
                min_size=22,
                color=INK,
                bold=True,
                spacing=4,
            )

        summary_y = top + count * (box_height + gap) + 8
        summary_y = min(summary_y, 1080)
        draw.rectangle((128, summary_y, 136, summary_y + 126), fill=GREEN)
        _draw_wrapped(
            draw,
            spec.summary,
            (164, summary_y),
            max_width=770,
            max_height=132,
            max_size=28,
            min_size=22,
            color=MUTED,
            spacing=6,
        )
        _draw_footer(draw, position)
        image.save(path, format="PNG", optimize=True)

    def _render_tradeoff(
        self,
        spec: TradeoffCardSpec,
        position: int,
        path: Path,
    ) -> None:
        image, draw = _canvas()
        _draw_header(draw, "TRADE-OFF", spec.title, CORAL)

        draw.rounded_rectangle(
            (72, 300, 1008, 535),
            radius=8,
            fill=WHITE,
            outline=LINE,
            width=2,
        )
        _draw_label(draw, "DECISION", 112, 334, CORAL)
        _draw_wrapped(
            draw,
            spec.decision,
            (112, 390),
            max_width=850,
            max_height=112,
            max_size=34,
            min_size=25,
            color=INK,
            bold=True,
            spacing=7,
        )

        draw.rounded_rectangle(
            (72, 590, 520, 1050),
            radius=8,
            fill="#EDF7F4",
            outline="#B8DDD4",
            width=2,
        )
        draw.rounded_rectangle(
            (560, 590, 1008, 1050),
            radius=8,
            fill="#FFF2EC",
            outline="#F2C5B8",
            width=2,
        )
        _draw_label(draw, "WHAT IT UNLOCKED", 112, 638, GREEN)
        _draw_wrapped(
            draw,
            spec.benefit,
            (112, 716),
            max_width=360,
            max_height=260,
            max_size=31,
            min_size=23,
            color=INK,
            spacing=8,
        )
        _draw_label(draw, "WHAT IT COST", 600, 638, CORAL)
        _draw_wrapped(
            draw,
            spec.cost,
            (600, 716),
            max_width=360,
            max_height=260,
            max_size=31,
            min_size=23,
            color=INK,
            spacing=8,
        )
        _draw_footer(draw, position)
        image.save(path, format="PNG", optimize=True)

    def _render_takeaway(
        self,
        spec: TakeawayCardSpec,
        position: int,
        path: Path,
    ) -> None:
        image, draw = _canvas()
        _draw_header(draw, "TAKEAWAYS", spec.title, GREEN)

        top = 330
        available = 760
        item_height = available // len(spec.items)
        for index, item in enumerate(spec.items):
            y = top + index * item_height
            draw.ellipse((88, y + 8, 158, y + 78), fill=GREEN)
            _draw_centered(
                draw,
                str(index + 1),
                (123, y + 43),
                _font(29, bold=True),
                WHITE,
            )
            _draw_wrapped(
                draw,
                item,
                (196, y),
                max_width=750,
                max_height=item_height - 28,
                max_size=34,
                min_size=24,
                color=INK,
                spacing=8,
            )
            if index < len(spec.items) - 1:
                line_y = y + item_height - 28
                draw.line((196, line_y, 956, line_y), fill=LINE, width=2)

        draw.rectangle((72, 1135, 1008, 1143), fill=YELLOW)
        _draw_footer(draw, position)
        image.save(path, format="PNG", optimize=True)


def _canvas():
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise PackageBuildError(
            "Pillow is required to render publishing package cards"
        ) from exc
    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), PAPER)
    return image, ImageDraw.Draw(image)


def _font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    candidates = (
        (
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        )
        if bold
        else (
            "/System/Library/Fonts/SFNS.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        )
    )
    linux = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    )
    for candidate in (*candidates, linux):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default(size=size)


def _draw_header(draw, eyebrow: str, title: str, accent: str) -> None:
    draw.rectangle((72, 78, 236, 88), fill=accent)
    _draw_label(draw, eyebrow, 72, 122, accent)
    _draw_wrapped(
        draw,
        title,
        (72, 172),
        max_width=920,
        max_height=110,
        max_size=52,
        min_size=36,
        color=INK,
        bold=True,
        spacing=6,
    )


def _draw_footer(draw, position: int) -> None:
    draw.line((72, 1245, 1008, 1245), fill=LINE, width=2)
    draw.text(
        (72, 1270),
        "BUILDLOG / LINKEDIN PACKAGE",
        font=_font(18, bold=True),
        fill=MUTED,
    )
    number = f"{position:02d}"
    bbox = draw.textbbox((0, 0), number, font=_font(18, bold=True))
    draw.text(
        (1008 - (bbox[2] - bbox[0]), 1270),
        number,
        font=_font(18, bold=True),
        fill=MUTED,
    )


def _draw_label(draw, text: str, x: int, y: int, color: str) -> None:
    draw.text((x, y), text, font=_font(20, bold=True), fill=color)


def _draw_centered(draw, text: str, center, font, color: str) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw.text(
        (center[0] - width / 2, center[1] - height / 2 - bbox[1]),
        text,
        font=font,
        fill=color,
    )


def _draw_wrapped(
    draw,
    text: str,
    position: tuple[int, int],
    *,
    max_width: int,
    max_height: int,
    max_size: int,
    min_size: int,
    color: str,
    spacing: int,
    bold: bool = False,
) -> None:
    font, lines = _fit_block(
        draw,
        text,
        max_width=max_width,
        max_height=max_height,
        max_size=max_size,
        min_size=min_size,
        spacing=spacing,
        bold=bold,
    )
    draw.multiline_text(
        position,
        "\n".join(lines),
        font=font,
        fill=color,
        spacing=spacing,
    )


def _fit_block(
    draw,
    text: str,
    *,
    max_width: int,
    max_height: int,
    max_size: int,
    min_size: int,
    spacing: int,
    bold: bool,
):
    for size in range(max_size, min_size - 1, -2):
        font = _font(size, bold=bold)
        lines = _wrap_lines(draw, text, font, max_width)
        if _lines_height(draw, lines, font, spacing) <= max_height:
            return font, lines
    font = _font(min_size, bold=bold)
    return font, _wrap_lines(draw, text, font, max_width)


def _wrap_lines(draw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _lines_height(draw, lines: list[str], font, spacing: int) -> int:
    bbox = draw.multiline_textbbox(
        (0, 0),
        "\n".join(lines),
        font=font,
        spacing=spacing,
    )
    return bbox[3] - bbox[1]


def _alt_text(spec: CardSpec) -> str:
    if isinstance(spec, TitleCardSpec):
        value = f"Title card: {spec.title}. {spec.subtitle}"
    elif isinstance(spec, ArchitectureCardSpec):
        value = (
            f"Architecture card: {spec.title}. Flow: "
            + " to ".join(spec.steps)
            + f". {spec.summary}"
        )
    elif isinstance(spec, TradeoffCardSpec):
        value = (
            f"Trade-off card: {spec.title}. Decision: {spec.decision}. "
            f"Benefit: {spec.benefit}. Cost: {spec.cost}."
        )
    else:
        value = f"Takeaway card: {spec.title}. " + " ".join(spec.items)
    return _truncate(value, 300)


def _truncate(value: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."
