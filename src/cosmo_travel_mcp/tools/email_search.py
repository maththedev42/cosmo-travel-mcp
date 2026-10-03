"""Email search planning — pure computation, no API calls, no network, no quota.

Generates structured search coordinates and query syntax to find booking confirmations,
receipts, and vouchers across a mailbox (including trash). This tool only generates
the search plan; it cannot connect to, read, or search anyone's mailbox.

If an email tool or connector (such as Gmail) is available in this session, execute
these queries with it.

Every query includes `includeTrash: true` in connector_args because confirmation receipts
are frequently moved to the trash folder, which email connectors exclude by default.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Sender Registry
# ---------------------------------------------------------------------------

# Observed in real mailboxes during 2026. Only entries with confirmed=True were
# verified in a real mailbox; unconfirmed entries are hypotheses never observed here.
EMAIL_SENDERS: tuple[dict[str, Any], ...] = (
    # Confirmed senders observed in real mailboxes (2026)
    {
        "kind": "flight",
        "sender": "info@info.latam.com",
        "confirmed": True,
        "seen": "2026-08-01",
        "what": "LATAM compra de passagem",
    },
    {
        "kind": "flight",
        "sender": "notifications@cns.copaair.com",
        "confirmed": True,
        "seen": "2026-08-01",
        "what": "Copa Airlines reserva de voo",
    },
    {
        "kind": "flight",
        "sender": "no-reply@info.email.aa.com",
        "confirmed": True,
        "seen": "2026-08-01",
        "what": "American Airlines confirmação de voo",
    },
    {
        "kind": "flight",
        "sender": "contato@123milhas.com",
        "confirmed": True,
        "seen": "2026-08-01",
        "what": "123milhas pedido e nota fiscal",
    },
    {
        "kind": "lodging",
        "sender": "automated@airbnb.com",
        "confirmed": True,
        "seen": "2026-08-01",
        "what": "Airbnb confirmação e recibo",
    },
    {
        "kind": "lodging",
        "sender": "express@airbnb.com",
        "confirmed": True,
        "seen": "2026-08-01",
        "what": "Airbnb conversa com anfitrião",
    },
    {
        "kind": "lodging",
        "sender": "reply@email-support.airbnb.com",
        "confirmed": True,
        "seen": "2026-08-01",
        "what": "Airbnb suporte",
    },
    {
        "kind": "lodging",
        "sender": "seucheckin@magikey.com.br",
        "confirmed": True,
        "seen": "2026-08-01",
        "what": "Magikey aviso de checkout de studio",
    },
    {
        "kind": "lodging",
        "sender": "sender@notifications.onfly.com.br",
        "confirmed": True,
        "seen": "2026-08-01",
        "what": "Onfly reserva corporativa de hotel",
    },
    # Unconfirmed platform hypotheses (never observed in this mailbox)
    {
        "kind": "flight",
        "sender": "voegol.com.br",
        "confirmed": False,
        "seen": "",
        "what": "Gol Linhas Aéreas (hipótese)",
    },
    {
        "kind": "flight",
        "sender": "voeazul.com.br",
        "confirmed": False,
        "seen": "",
        "what": "Azul Linhas Aéreas (hipótese)",
    },
    {
        "kind": "flight",
        "sender": "united.com",
        "confirmed": False,
        "seen": "",
        "what": "United Airlines (hipótese)",
    },
    {
        "kind": "flight",
        "sender": "decolar.com",
        "confirmed": False,
        "seen": "",
        "what": "Decolar voos (hipótese)",
    },
    {
        "kind": "flight",
        "sender": "despegar.com",
        "confirmed": False,
        "seen": "",
        "what": "Despegar voos (hipótese)",
    },
    {
        "kind": "lodging",
        "sender": "booking.com",
        "confirmed": False,
        "seen": "",
        "what": "Booking.com (hipótese)",
    },
    {
        "kind": "lodging",
        "sender": "expedia.com",
        "confirmed": False,
        "seen": "",
        "what": "Expedia (hipótese)",
    },
    {
        "kind": "lodging",
        "sender": "hoteis.com",
        "confirmed": False,
        "seen": "",
        "what": "Hoteis.com (hipótese)",
    },
    {
        "kind": "lodging",
        "sender": "hostelworld.com",
        "confirmed": False,
        "seen": "",
        "what": "Hostelworld (hipótese)",
    },
    {
        "kind": "lodging",
        "sender": "agoda.com",
        "confirmed": False,
        "seen": "",
        "what": "Agoda (hipótese)",
    },
    {
        "kind": "lodging",
        "sender": "decolar.com",
        "confirmed": False,
        "seen": "",
        "what": "Decolar hospedagem (hipótese)",
    },
    {
        "kind": "lodging",
        "sender": "despegar.com",
        "confirmed": False,
        "seen": "",
        "what": "Despegar hospedagem (hipótese)",
    },
)

VALID_KINDS: tuple[str, ...] = ("flight", "lodging", "car", "insurance", "ticket")

KEYWORD_EXPRESSION = (
    '("reserva confirmada" OR "booking confirmation" OR "recibo" OR "voucher" OR "sua viagem")'
)

# Field extraction targets matching skills/plan-a-trip/ledger.py schema
EXTRACT_FIELDS: dict[str, list[str]] = {
    "flight": [
        "segments",
        "locator",
        "seller",
        "paid",
        "baggage",
    ],
    "lodging": [
        "check_in",
        "check_out",
        "booking.seller",
        "booking.locator",
        "booking.source",
        "booking.paid",
        "booking.refundable_until",
        "booking.paid_unmeasured_why",
    ],
    "car": [
        "company",
        "locator",
        "pickup_datetime",
        "dropoff_datetime",
        "pickup_location",
        "dropoff_location",
        "paid",
        "cancellation_policy",
    ],
    "insurance": [
        "policy_number",
        "insurer",
        "coverage_start",
        "coverage_end",
        "destination",
        "paid",
    ],
    "ticket": [
        "event_name",
        "locator",
        "event_date",
        "venue",
        "tickets_count",
        "paid",
    ],
}

READING_INSTRUCTIONS: list[str] = [
    (
        "Se get_thread falhar com erro de permissão (comum em mensagens na lixeira), "
        "repita com get_message na mesma mensagem (na lixeira o thread pode falhar "
        "enquanto a leitura direta da mensagem funciona)."
    ),
    (
        "Se o corpo de texto vier em branco, solicite e inspecione o HTML da mensagem."
    ),
    (
        "Anexo PDF aparece apenas como metadado e não é legível pelo conector: "
        "declare explicitamente qualquer anexo PDF não lido como lacuna aberta em vez de silêncio."
    ),
    (
        "Casar e-mail a viagem exige conferir datas e destino no corpo da mensagem: "
        "remetente e palavra-chave sozinhos já atribuíram cancelamento de show a uma viagem diferente."
    ),
    (
        "Resultado vazio é afirmação: relate remetentes consultados, janela de datas e "
        "confirme que a lixeira entrou na busca antes de concluir que não há reserva."
    ),
]


async def plan_email_search(
    kinds: list[str],
    destination: str = "",
    window_start: str = "",
    window_end: str = "",
    known_locators: list[str] | None = None,
    include_unconfirmed_senders: bool = True,
) -> dict[str, Any]:
    """Generate search coordinates and query syntax to find travel bookings in an email mailbox.

    Costs nothing — no API calls, no network, and no quota used. This tool only generates
    a search plan; it cannot connect to, read, or search anyone's mailbox.

    This server cannot see or call other MCP servers (such as a Gmail connector). If an
    email tool is available in this session, execute these queries with it.

    Every query includes `includeTrash: true` in connector_args because confirmation receipts
    are frequently moved to the trash folder, which email connectors exclude by default.

    Args:
        kinds: List of categories to search. Subset of ["flight", "lodging", "car", "insurance", "ticket"].
        destination: City or country to search as a broad keyword term (never combined inside sender queries).
        window_start: ISO date (YYYY-MM-DD) of the earliest probable purchase date (not travel date).
        window_end: ISO date (YYYY-MM-DD) of the latest probable purchase date.
        known_locators: List of alphanumeric reservation codes (5-20 characters) to locate known purchases.
        include_unconfirmed_senders: Whether to generate pass 2 queries for unconfirmed hypothesis senders.

    Returns:
        Structured plan containing `kinds`, `queries`, `reading`, `extract`, and `limits`.
    """
    # 1. Validate kinds
    if not isinstance(kinds, list) or not kinds:
        raise ValueError(
            f"kinds must be a non-empty list; valid kinds are: {', '.join(sorted(VALID_KINDS))}"
        )
    for k in kinds:
        if k not in VALID_KINDS:
            raise ValueError(
                f"unknown kind {k!r}; valid kinds are: {', '.join(sorted(VALID_KINDS))}"
            )

    # 2. Validate dates
    after_str = ""
    before_str = ""
    if window_start:
        try:
            d_start = datetime.strptime(window_start, "%Y-%m-%d")
            after_str = d_start.strftime("%Y/%m/%d")
        except ValueError:
            raise ValueError(
                f"window_start must be in YYYY-MM-DD format, got {window_start!r}"
            )

    if window_end:
        try:
            d_end = datetime.strptime(window_end, "%Y-%m-%d")
            before_str = d_end.strftime("%Y/%m/%d")
        except ValueError:
            raise ValueError(
                f"window_end must be in YYYY-MM-DD format, got {window_end!r}"
            )

    if window_start and window_end and window_end < window_start:
        raise ValueError(
            f"window_end ({window_end}) cannot be before window_start ({window_start})"
        )

    # 3. Validate known_locators
    locators: list[str] = []
    if known_locators:
        if not isinstance(known_locators, list):
            raise ValueError("known_locators must be a list of alphanumeric strings")
        for loc in known_locators:
            if not isinstance(loc, str) or not loc.isalnum() or not (5 <= len(loc) <= 20):
                raise ValueError(
                    f"known locator must be 5-20 alphanumeric characters, got {loc!r}"
                )
            locators.append(loc)

    queries: list[dict[str, Any]] = []

    # Date clause suffix
    date_parts: list[str] = []
    if after_str:
        date_parts.append(f"after:{after_str}")
    if before_str:
        date_parts.append(f"before:{before_str}")
    date_suffix = f" {' '.join(date_parts)}" if date_parts else ""

    # Pass 1: Confirmed senders per kind
    for kind in kinds:
        confirmed = [
            s["sender"]
            for s in EMAIL_SENDERS
            if s["kind"] == kind and s["confirmed"] is True
        ]
        if confirmed:
            senders_expr = (
                f"from:({ ' OR '.join(confirmed) })"
                if len(confirmed) > 1
                else f"from:{confirmed[0]}"
            )
            gmail_query = f"{senders_expr}{date_suffix}"
            queries.append({
                "kind": kind,
                "pass": 1,
                "confirmed_senders": True,
                "gmail_query": gmail_query,
                "connector_args": {"includeTrash": True},
                "why": f"remetentes confirmados de {kind} observados em caixa real",
            })

    # Pass 2: Unconfirmed hypotheses per kind
    if include_unconfirmed_senders:
        for kind in kinds:
            unconfirmed = [
                s["sender"]
                for s in EMAIL_SENDERS
                if s["kind"] == kind and s["confirmed"] is False
            ]
            if unconfirmed:
                senders_expr = (
                    f"from:({ ' OR '.join(unconfirmed) })"
                    if len(unconfirmed) > 1
                    else f"from:{unconfirmed[0]}"
                )
                gmail_query = f"{senders_expr}{date_suffix}"
                queries.append({
                    "kind": kind,
                    "pass": 2,
                    "confirmed_senders": False,
                    "gmail_query": gmail_query,
                    "connector_args": {"includeTrash": True},
                    "why": "hipótese: remetente nunca visto numa caixa real",
                })

    # Pass 3: Broad keyword query
    kw_query = f"{KEYWORD_EXPRESSION}{date_suffix}"
    queries.append({
        "kind": "all",
        "pass": 3,
        "confirmed_senders": False,
        "gmail_query": kw_query,
        "connector_args": {"includeTrash": True},
        "why": "cobertura ampla por palavras-chave",
    })

    # Pass 3b: Destination query (broad coverage, separate from senders)
    dest_clean = destination.strip()
    if dest_clean:
        dest_term = f'"{dest_clean}"'
        dest_query = f"{dest_term} {KEYWORD_EXPRESSION}{date_suffix}"
        queries.append({
            "kind": "destination",
            "pass": 3,
            "confirmed_senders": False,
            "gmail_query": dest_query,
            "connector_args": {"includeTrash": True},
            "why": f"cobertura ampla por destino ({dest_clean})",
        })

    # Known locators: one query per locator
    for loc in locators:
        queries.append({
            "kind": "locator",
            "pass": 1,
            "confirmed_senders": False,
            "gmail_query": f'"{loc}"',
            "connector_args": {"includeTrash": True},
            "why": f"localizador conhecido {loc}",
        })

    # Build limits
    limits: list[str] = [
        "O plano de busca é um ponto de partida e não garante cobertura completa.",
        "O registro de remetentes reflete apenas plataformas observadas em caixas reais; remetentes não confirmados são hipóteses.",
        "O servidor não lê e-mail nem executa buscas: utilize as consultas geradas no conector de e-mail disponível.",
    ]
    if not window_start and not window_end:
        limits.append(
            "Busca irrestrita por data: nenhuma janela foi informada; considere fornecer window_start e window_end para restringir o período da busca."
        )

    # Build extract
    extract = {k: EXTRACT_FIELDS[k] for k in kinds}

    return {
        "kinds": kinds,
        "queries": queries,
        "reading": READING_INSTRUCTIONS,
        "extract": extract,
        "limits": limits,
    }


def register(mcp: Any) -> None:
    """Register email search tool on a FastMCP instance."""
    mcp.tool()(plan_email_search)
