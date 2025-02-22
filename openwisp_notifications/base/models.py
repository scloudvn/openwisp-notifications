import logging

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.db import models
from django.db.models.constraints import UniqueConstraint
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.html import mark_safe
from django.utils.translation import ugettext_lazy as _
from markdown import markdown
from notifications.base.models import AbstractNotification as BaseNotification
from swapper import get_model_name

from openwisp_notifications import settings as app_settings
from openwisp_notifications.exceptions import NotificationRenderException
from openwisp_notifications.types import (
    NOTIFICATION_CHOICES,
    get_notification_configuration,
)
from openwisp_notifications.utils import _get_absolute_url, _get_object_link
from openwisp_utils.base import UUIDModel

logger = logging.getLogger(__name__)


class AbstractNotification(UUIDModel, BaseNotification):
    CACHE_KEY_PREFIX = 'ow-notifications-'
    type = models.CharField(max_length=30, null=True, choices=NOTIFICATION_CHOICES)
    _actor = BaseNotification.actor
    _action_object = BaseNotification.action_object
    _target = BaseNotification.target

    class Meta(BaseNotification.Meta):
        abstract = True

    def __init__(self, *args, **kwargs):
        related_objs = [
            (opt, kwargs.pop(opt, None)) for opt in ('target', 'action_object', 'actor')
        ]
        super().__init__(*args, **kwargs)
        for opt, obj in related_objs:
            if obj is not None:
                setattr(self, f'{opt}_object_id', obj.pk)
                setattr(
                    self, f'{opt}_content_type', ContentType.objects.get_for_model(obj),
                )

    def __str__(self):
        return self.timesince()

    @classmethod
    def _cache_key(cls, *args):
        args = map(str, args)
        key = '-'.join(args)
        return f'{cls.CACHE_KEY_PREFIX}{key}'

    @classmethod
    def count_cache_key(cls, user_pk):
        return cls._cache_key(f'unread-{user_pk}')

    @classmethod
    def invalidate_unread_cache(cls, user):
        """
        Invalidate unread cache for user.
        """
        cache.delete(cls.count_cache_key(user.pk))

    @property
    def actor_url(self):
        return _get_object_link(self, field='actor', url_only=True, absolute_url=True)

    @property
    def action_url(self):
        return _get_object_link(
            self, field='action_object', url_only=True, absolute_url=True,
        )

    @property
    def target_url(self):
        return _get_object_link(self, field='target', url_only=True, absolute_url=True)

    @cached_property
    def message(self):
        return self.get_message()

    @property
    def email_message(self):
        return self.get_message(email_message=True)

    def get_message(self, email_message=False):
        if self.type:
            # setting links in notification object for message rendering
            self.actor_link = self.actor_url
            self.action_link = self.action_url
            self.target_link = (
                self.target_url if not email_message else self.redirect_view_url
            )

            config = get_notification_configuration(self.type)
            try:
                data = self.data or {}
                if 'message' in config:
                    md_text = config['message'].format(notification=self, **data)
                else:
                    md_text = render_to_string(
                        config['message_template'], context=dict(notification=self)
                    ).strip()
            except (AttributeError, KeyError) as e:
                from openwisp_notifications.tasks import delete_notification

                logger.error(e)
                delete_notification.delay(notification_id=self.pk)
                raise NotificationRenderException(
                    'Error in rendering notification message.'
                )
            # clean up
            self.actor_link = self.action_link = self.target_link = None
            return mark_safe(markdown(md_text))
        else:
            return self.description

    @cached_property
    def email_subject(self):
        if self.type:
            try:
                config = get_notification_configuration(self.type)
                data = self.data or {}
                return config['email_subject'].format(
                    site=Site.objects.get_current(), notification=self, **data
                )
            except (AttributeError, KeyError) as e:
                from openwisp_notifications.tasks import delete_notification

                logger.error(e)
                delete_notification.delay(notification_id=self.pk)
                raise NotificationRenderException(
                    'Error while generating notification email'
                )
        elif self.data.get('email_subject', None):
            return self.data.get('email_subject')
        else:
            return self.message

    def _related_object(self, field):
        obj_id = getattr(self, f'{field}_object_id')
        obj_content_type_id = getattr(self, f'{field}_content_type_id')
        if not obj_id:
            return
        cache_key = self._cache_key(obj_content_type_id, obj_id)
        obj = cache.get(cache_key)
        if not obj:
            obj = getattr(self, f'_{field}')
            cache.set(
                cache_key,
                obj,
                timeout=app_settings.OPENWISP_NOTIFICATIONS_CACHE_TIMEOUT,
            )
        return obj

    @cached_property
    def actor(self):
        return self._related_object('actor')

    @cached_property
    def action_object(self):
        return self._related_object('action_object')

    @cached_property
    def target(self):
        return self._related_object('target')

    @property
    def redirect_view_url(self):
        return _get_absolute_url(
            reverse('notifications:notification_read_redirect', args=(self.pk,))
        )


class AbstractNotificationSetting(UUIDModel):
    _RECEIVE_HELP = (
        'Note: Non-superadmin users receive '
        'notifications only for organizations '
        'of which they are member of.'
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    type = models.CharField(
        max_length=30,
        null=True,
        choices=NOTIFICATION_CHOICES,
        verbose_name='Notification Type',
    )
    organization = models.ForeignKey(
        get_model_name('openwisp_users', 'Organization'), on_delete=models.CASCADE,
    )
    web = models.BooleanField(
        _('web notifications'), null=True, blank=True, help_text=_(_RECEIVE_HELP)
    )
    email = models.BooleanField(
        _('email notifications'), null=True, blank=True, help_text=_(_RECEIVE_HELP)
    )
    deleted = models.BooleanField(_('Delete'), null=True, blank=True, default=False)

    class Meta:
        abstract = True
        constraints = [
            UniqueConstraint(
                fields=['organization', 'type', 'user'],
                name='unique_notification_setting',
            ),
        ]
        verbose_name = _('user notification settings')
        verbose_name_plural = verbose_name
        ordering = ['organization', 'type']
        indexes = [
            models.Index(fields=['type', 'organization']),
        ]

    def __str__(self):
        return '{type} - {organization}'.format(
            type=self.type_config['verbose_name'], organization=self.organization,
        )

    def save(self, *args, **kwargs):
        if not self.web_notification:
            self.email = self.web_notification
        return super().save(*args, **kwargs)

    def full_clean(self, *args, **kwargs):
        if self.email == self.type_config['email_notification']:
            self.email = None
        if self.web == self.type_config['web_notification']:
            self.web = None
        return super().full_clean(*args, **kwargs)

    @property
    def type_config(self):
        return get_notification_configuration(self.type)

    @property
    def email_notification(self):
        if self.email is not None:
            return self.email
        return self.type_config.get('email_notification')

    @property
    def web_notification(self):
        if self.web is not None:
            return self.web
        return self.type_config.get('web_notification')


class AbstractIgnoreObjectNotification(UUIDModel):
    """
    This model stores information about ignoring notification
    from a specific object for a user. Any instance of the model
    should be only stored until "valid_till" expires.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    object_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=255)
    object = GenericForeignKey('object_content_type', 'object_id')
    valid_till = models.DateTimeField(null=True)

    class Meta:
        abstract = True
        ordering = ['valid_till']
