from aiogram.fsm.state import State, StatesGroup


class EditPage(StatesGroup):
    waiting_text = State()
    confirm_text = State()
    waiting_title = State()
    confirm_title = State()


class CreatePage(StatesGroup):
    title = State()
    text = State()
    title_kk = State()
    text_kk = State()
    confirm = State()


class AddButton(StatesGroup):
    text = State()
    text_kk = State()
    action = State()
    target = State()
    confirm = State()


class EditButton(StatesGroup):
    value = State()


class AddFAQ(StatesGroup):
    question = State()
    answer = State()
    question_kk = State()
    answer_kk = State()
    confirm = State()


class EditFAQ(StatesGroup):
    value = State()


class ImportFAQ(StatesGroup):
    file = State()
    preview = State()
    translation_preview = State()
    translation_edit = State()


class TranslatePage(StatesGroup):
    preview = State()
    edit = State()


class AddAdmin(StatesGroup):
    telegram_id = State()
    role = State()
    confirm = State()


class CreateBroadcast(StatesGroup):
    text = State()
    media = State()
    confirm = State()


class AddAttachment(StatesGroup):
    file = State()
    caption_ru = State()
    caption_kk = State()
    confirm = State()


class EditAttachment(StatesGroup):
    caption = State()


class TestAIRetrieval(StatesGroup):
    query = State()


class ModerateChannelPost(StatesGroup):
    preview = State()
    edit = State()
