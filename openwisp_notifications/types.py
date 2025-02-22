from django.core.exceptions import ImproperlyConfigured
from django.template.loader import get_template

NOTIFICATION_TYPES = {
    'default': {
        'level': 'info',
        'verb': 'default verb',
        'verbose_name': 'Default Type',
        'email_subject': '[{site.name}] Default Notification Subject',
        'message': (
            'Default notification with {notification.verb} and level {notification.level}'
            ' by [{notification.actor}]({notification.actor_link})'
        ),
        'message_template': 'openwisp_notifications/default_message.md',
        'email_notification': True,
        'web_notification': True,
    },
}

NOTIFICATION_CHOICES = [('default', 'Default Type')]
NOTIFICATION_ASSOCIATED_MODELS = set()


def get_notification_configuration(notification_type):
    if not notification_type:
        return {}
    try:
        return NOTIFICATION_TYPES[notification_type]
    except KeyError:
        raise ImproperlyConfigured(f'No such Notification Type, {notification_type}')


def _validate_notification_type(type_config):
    options = type_config.keys()
    assert 'level' in options
    assert 'verb' in options
    assert 'email_subject' in options
    assert ('message' in options) or ('message_template' in options)

    if 'message_template' in options:
        get_template(type_config['message_template'])

    if 'email_notification' not in options:
        type_config['email_notification'] = True

    if 'web_notification' not in options:
        type_config['web_notification'] = True

    return type_config


def register_notification_type(type_name, type_config, models=[]):
    """
    Registers a new notification type.
    """
    if not isinstance(type_name, str):
        raise ImproperlyConfigured('Notification Type name should be type `str`.')
    if not isinstance(type_config, dict):
        raise ImproperlyConfigured(
            'Notification Type configuration should be type `dict`.'
        )
    if type_name in NOTIFICATION_TYPES:
        raise ImproperlyConfigured(
            f'{type_name} is an already registered Notification Type.'
        )

    validated_type_config = _validate_notification_type(type_config)
    NOTIFICATION_TYPES.update({type_name: validated_type_config})
    _register_notification_choice(type_name, validated_type_config)
    NOTIFICATION_ASSOCIATED_MODELS.update(models)


def unregister_notification_type(type_name):
    if not isinstance(type_name, str):
        raise ImproperlyConfigured('Notification Type name should be type `str`')
    if type_name not in NOTIFICATION_TYPES:
        raise ImproperlyConfigured(f'No such Notification Type, {type_name}')

    NOTIFICATION_TYPES.pop(type_name)
    _unregister_notification_choice(type_name)


def _register_notification_choice(type_name, type_config):
    name = type_config.get('verbose_name', type_name)
    NOTIFICATION_CHOICES.append((type_name, name))


def _unregister_notification_choice(notification_type):
    for index, (key, name) in enumerate(NOTIFICATION_CHOICES):
        if key == notification_type:
            NOTIFICATION_CHOICES.pop(index)
            return
    raise ImproperlyConfigured(f'No such Notification Choice {notification_type}')
