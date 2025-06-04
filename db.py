from tortoise import Tortoise, fields, models
from tortoise.exceptions import DoesNotExist
import datetime

class User(models.Model):
    id = fields.BigIntField(pk=True)
    username = fields.CharField(max_length=255, null=True)
    first_use = fields.DatetimeField(auto_now_add=True)
    model = fields.CharField(max_length=64, default="gpt-4.1")
    current_dialog = fields.ForeignKeyField(
        'models.Dialog', related_name='active_user', null=True, on_delete=fields.SET_NULL
    )

    class Meta:
        table = "users"

class Dialog(models.Model):
    id = fields.IntField(pk=True)
    start_time = fields.DatetimeField(auto_now_add=True)
    model_used = fields.CharField(max_length=64)

    class Meta:
        table = "dialogs"

class DialogMessage(models.Model):
    id = fields.IntField(pk=True)
    dialog = fields.ForeignKeyField('models.Dialog', related_name='messages')
    text = fields.TextField()
    sender = fields.CharField(max_length=10) 
    timestamp = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "dialog_messages"

async def init_db():
    await Tortoise.init(
        db_url='sqlite://db.sqlite3',
        modules={'models': ['db']}
    )
    await Tortoise.generate_schemas()

async def get_or_create_user_and_dialog(user_id: int, username: str | None, preferred_model_key: str = "gpt-4.1"):
    user, created = await User.get_or_create(id=user_id, defaults={"username": username, "model": preferred_model_key})
    if created:
        dialog = await Dialog.create(user=user, model_used=user.model)
        user.current_dialog = dialog
        await user.save()
    else:
        dialog = await user.current_dialog
        if not dialog:
            dialog = await Dialog.create(user=user, model_used=user.model)
            user.current_dialog = dialog
            await user.save()
    return user, dialog 