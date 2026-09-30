from aiogram.fsm.state import State, StatesGroup


class AskAI(StatesGroup):
    question = State()
