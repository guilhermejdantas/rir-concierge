"""Curated trivia question bank + participant registry for the festival quiz.

25 questions — one per scheduled round (13 on Thu 10/09 09:00–21:00, then
12 on Fri 11/09 09:00–20:00).
"""
from __future__ import annotations

from models import QuizQuestion

# --------------------------------------------------------------------------- #
# Participants. WhatsApp group members send from either their phone JID        #
# (``<number>@s.whatsapp.net``) or the newer linked-id (``<lid>@lid``); map    #
# BOTH forms to a display name. Unknown senders fall back to their pushName.   #
# --------------------------------------------------------------------------- #
PARTICIPANTS: dict[str, str] = {
    # phone numbers
    "5511996213464": "Guilherme",
    "5511974842046": "Raquel",
    "5511982571245": "David",
    # linked ids (same people, @lid form seen in this group)
    "117235561558087": "Guilherme",
    "178958469484596": "Raquel",
    "122153349439656": "David",
}

# Everyone who should appear on the leaderboard from the start (0 pts).
LEADERBOARD_SEED: tuple[str, ...] = ("Guilherme", "Raquel", "David")

# Display name -> phone number, for building real WhatsApp @-mentions.
NAME_TO_NUMBER: dict[str, str] = {
    "Guilherme": "5511996213464",
    "Raquel": "5511974842046",
    "David": "5511982571245",
}


def tag(name: str) -> str:
    """Return a mention token: ``@<number>`` when known (renders + notifies),
    else ``@Name`` as plain text."""
    number = NAME_TO_NUMBER.get(name)
    return f"@{number}" if number else f"@{name}"


def resolve_participant(jid: str | None, push_name: str | None = None) -> str:
    """Return a display name for a sender JID, falling back to pushName."""
    if jid:
        handle = jid.split("@", 1)[0].split(":", 1)[0].lstrip("+")
        if handle in PARTICIPANTS:
            return PARTICIPANTS[handle]
    return (push_name or "").strip() or "Alguém"


QUESTIONS: list[QuizQuestion] = [
    QuizQuestion(
        id=1, topic="Rock in Rio 1985",
        prompt="Qual banda tocou no primeiro Rock in Rio sob chuva torrencial em show épico?",
        options={"A": "Queen", "B": "Led Zeppelin", "C": "AC/DC"}, correct="A",
    ),
    QuizQuestion(
        id=2, topic="Jamiroquai",
        prompt="Qual é a marca registrada visual do vocalista Jay Kay nos palcos e clipes?",
        options={"A": "Óculos espelhados", "B": "Chapéus e elmos extravagantes",
                 "C": "Jaquetas de couro amarelo"}, correct="B",
    ),
    QuizQuestion(
        id=3, topic="Maroon 5",
        prompt="Qual foi o primeiro grande hit mundial da banda, lançado em 2002?",
        options={"A": "Sugar", "B": "This Love", "C": "Moves Like Jagger"}, correct="B",
    ),
    QuizQuestion(
        id=4, topic="Alok",
        prompt="Em qual palco Alok se apresenta na sexta-feira, dia 11/09?",
        options={"A": "Palco Sunset", "B": "Palco Mundo", "C": "New Dance Order"}, correct="B",
    ),
    QuizQuestion(
        id=5, topic="Ivete Sangalo",
        prompt="Ivete é a recordista brasileira em participações no Rock in Rio (Brasil + Lisboa). Quantas edições, aproximadamente?",
        options={"A": "Mais de 15 edições", "B": "5 edições", "C": "8 edições"}, correct="A",
    ),
    QuizQuestion(
        id=6, topic="Mumford & Sons",
        prompt="Qual instrumento folclórico é marca registrada do som da banda?",
        options={"A": "Banjo", "B": "Gaita de fole", "C": "Cítara"}, correct="A",
    ),
    QuizQuestion(
        id=7, topic="Twenty One Pilots",
        prompt="De qual cidade e estado americano vem a dupla Tyler e Josh?",
        options={"A": "Columbus, Ohio", "B": "Seattle, Washington", "C": "Austin, Texas"}, correct="A",
    ),
    QuizQuestion(
        id=8, topic="Demi Lovato",
        prompt="Qual álbum marcou a guinada de Demi para o Pop-Punk/Rock?",
        options={"A": "Confident", "B": "HOLY FVCK", "C": "Tell Me You Love Me"}, correct="B",
    ),
    QuizQuestion(
        id=9, topic="Pedro Sampaio",
        prompt="Qual é o bordão clássico que Pedro Sampaio fala na abertura de suas músicas?",
        options={"A": "\"Chama que vem!\"", "B": "\"PE-DRO SAM-PAI-O!\"", "C": "\"Solta o som!\""}, correct="B",
    ),
    QuizQuestion(
        id=10, topic="Timbalada",
        prompt="Qual ritmo, criado por Carlinhos Brown, a Timbalada popularizou pelo mundo?",
        options={"A": "Axé tradicional", "B": "Samba-reggae com timbau", "C": "Frevo elétrico"}, correct="B",
    ),
    QuizQuestion(
        id=11, topic="Joelma",
        prompt="Qual banda do Pará consagrou Joelma, com botas de cano alto e tacacá?",
        options={"A": "Banda Calypso", "B": "Mastruz com Leite", "C": "Companhia do Calypso"}, correct="A",
    ),
    QuizQuestion(
        id=12, topic="Halsey",
        prompt="O nome artístico \"Halsey\" é um anagrama de qual nome verdadeiro da cantora?",
        options={"A": "Ashley", "B": "Hailey", "C": "Rachel"}, correct="A",
    ),
    QuizQuestion(
        id=13, topic="Marina Sena",
        prompt="Qual música de 2021 transformou Marina Sena em fenômeno viral no Brasil?",
        options={"A": "Dano Sarrada", "B": "Por Supuesto", "C": "Que Tal"}, correct="B",
    ),
    QuizQuestion(
        id=14, topic="Dennis DJ",
        prompt="Dennis é o produtor por trás de qual hino do funk dos anos 2000?",
        options={"A": "Glamurosa", "B": "Cerol na Mão", "C": "Rap da Felicidade"}, correct="B",
    ),
    QuizQuestion(
        id=15, topic="John Summit",
        prompt="Antes de virar DJ superstar do Tech House, qual era a profissão de John Summit?",
        options={"A": "Contador (CPA)", "B": "Engenheiro civil", "C": "Advogado"}, correct="A",
    ),
    QuizQuestion(
        id=16, topic="Rock in Rio",
        prompt="Em que ano aconteceu a primeira edição do Rock in Rio, no Rio de Janeiro?",
        options={"A": "1985", "B": "1991", "C": "1980"}, correct="A",
    ),
    QuizQuestion(
        id=17, topic="Cidade do Rock",
        prompt="Qual é o nome pelo qual o espaço do festival no Parque Olímpico é conhecido?",
        options={"A": "Cidade do Rock", "B": "Vila do Rock", "C": "Arena Rock"}, correct="A",
    ),
    QuizQuestion(
        id=18, topic="Maroon 5",
        prompt="Adam Levine foi técnico por muitas temporadas de qual reality musical dos EUA?",
        options={"A": "The Voice", "B": "American Idol", "C": "The X Factor"}, correct="A",
    ),
    QuizQuestion(
        id=19, topic="Jamiroquai",
        prompt="O nome \"Jamiroquai\" une \"jam\" ao nome de qual confederação indígena norte-americana?",
        options={"A": "Iroquois", "B": "Apache", "C": "Cherokee"}, correct="A",
    ),
    QuizQuestion(
        id=20, topic="Alok",
        prompt="Os pais de Alok formavam qual dupla pioneira da música eletrônica no Brasil?",
        options={"A": "Elohim", "B": "Dazaranha", "C": "Skank"}, correct="A",
    ),
    QuizQuestion(
        id=21, topic="Mumford & Sons",
        prompt="Qual álbum de estreia (2009) traz \"Little Lion Man\"?",
        options={"A": "Sigh No More", "B": "Babel", "C": "Delta"}, correct="A",
    ),
    QuizQuestion(
        id=22, topic="Halsey",
        prompt="Com qual álbum conceitual Halsey estreou, em 2015?",
        options={"A": "Badlands", "B": "Manic", "C": "Hopeless Fountain Kingdom"}, correct="A",
    ),
    QuizQuestion(
        id=23, topic="Demi Lovato",
        prompt="Demi e Selena Gomez se conheceram nas gravações de qual programa infantil?",
        options={"A": "Barney & Friends", "B": "Hannah Montana", "C": "Os Feiticeiros de Waverly Place"}, correct="A",
    ),
    QuizQuestion(
        id=24, topic="Ivete Sangalo",
        prompt="Antes da carreira solo, Ivete foi vocalista de qual banda de axé?",
        options={"A": "Banda Eva", "B": "Chiclete com Banana", "C": "Asa de Águia"}, correct="A",
    ),
    QuizQuestion(
        id=25, topic="Marina Sena",
        prompt="Marina Sena integrou qual banda mineira antes da carreira solo?",
        options={"A": "Rosa Neon", "B": "Bala Desejo", "C": "Boogarins"}, correct="A",
    ),
]

assert len(QUESTIONS) == 25, "expected exactly 25 seeded questions"
assert len({q.id for q in QUESTIONS}) == 25, "question ids must be unique"
