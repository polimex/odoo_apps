# -*- coding: utf-8 -*-
from odoo import api, fields, models, exceptions
from datetime import timedelta, datetime
from enum import Enum


class OwnerType(Enum):
    Employee = 1
    Contact = 2


class HrRfidCard(models.Model):
    _name = 'hr.rfid.card'
    _description = 'Card'
    _inherit = ['mail.thread']

    def _get_cur_employee_id(self):
        return self.env.context.get('employee_id', None)

    name = fields.Char(
        compute='_compute_card_name',
    )

    number = fields.Char(
        string='Card Number',
        required=True,
        limit=10,
        index=True,
        track_visibility='onchange',
    )

    card_type = fields.Many2one(
        'hr.rfid.card.type',
        string='Card type',
        help='Only doors that support this type will be able to open this card',
        default=lambda self: self.env.ref('hr_rfid.hr_rfid_card_type_def').id,
        track_visibility='onchange',
    )

    employee_id = fields.Many2one(
        'hr.employee',
        string='Card Owner (Employee)',
        default=_get_cur_employee_id,
        track_visibility='onchange',
    )

    contact_id = fields.Many2one(
        'res.partner',
        string='Card Owner (Partner)',
        ondelete='cascade',
        default=0,
        track_visibility='onchange',
    )

    user_event_ids = fields.One2many(
        'hr.rfid.event.user',
        'card_id',
        string='Events',
        help='Events concerning this user',
    )

    activate_on = fields.Datetime(
        string='Activate on',
        help='Date and time the card will be activated on',
        track_visibility='onchange',
        default=lambda self: datetime.now(),
    )

    deactivate_on = fields.Datetime(
        string='Deactivate on',
        help='Date and time the card will be deactivated on',
        track_visibility='onchange',
    )

    card_active = fields.Boolean(
        string='Active',
        help='Whether the card is active or not',
        track_visibility='onchange',
        default=True,
    )

    cloud_card = fields.Boolean(
        string='Cloud Card',
        help='A cloud card will not be added to controllers that are in the "externalDB" mode.',
        track_visibility='onchange',
        default=True,
        required=True,
    )

    door_rel_ids = fields.One2many(
        'hr.rfid.card.door.rel',
        'card_id',
        string='Doors',
        help='Doors this card has access to',
    )

    pin_code = fields.Char(compute='_compute_pin_code')

    def get_owner(self):
        if len(self.employee_id) == 1:
            return self.employee_id
        return self.contact_id

    def get_owner_type(self):
        if len(self.employee_id) == 1:
            return OwnerType.Employee
        return OwnerType.Contact

    def get_potential_access_doors(self, access_groups=None):
        """
        Returns a list of tuples (door, time_schedule) the card potentially has access to
        """
        if access_groups is None:
            owner = self.get_owner()
            access_groups = owner.hr_rfid_access_group_ids.mapped('access_group_id')
        door_rel_ids = access_groups.mapped('all_door_ids')
        return [ (rel.door_id, rel.time_schedule_id) for rel in door_rel_ids ]

    def door_compatible(self, door_id):
        return self.card_type == door_id.card_type \
               and (self.cloud_card is False or door_id.controller_id.external_db is False)

    def card_ready(self):
        return self.card_active

    @api.depends('employee_id', 'contact_id')
    @api.multi
    def _compute_pin_code(self):
        for card in self:
            card.pin_code = card.get_owner().hr_rfid_pin_code

    @api.one
    def toggle_card_active(self):
        self.card_active = not self.card_active

    @api.multi
    @api.constrains('employee_id', 'contact_id')
    def _check_user(self):
        for card in self:
            if card.employee_id is not None and card.contact_id is not None:
                if card.employee_id == card.contact_id or \
                   (len(card.employee_id) > 0 and len(card.contact_id) > 0):
                    raise exceptions.ValidationError('Card user and contact cannot both be set '
                                                     'in the same time, and cannot both be empty.')

    @api.multi
    @api.constrains('number')
    def _check_number(self):
        for card in self:
            if len(card.number) != 10:
                raise exceptions.ValidationError('Card number must be exactly 10 digits')

            try:
                for char in card.number:
                    int(char, 10)
            except ValueError:
                raise exceptions.ValidationError('Card number digits must be from 0 to 9')

    @api.multi
    def _compute_card_name(self):
        for record in self:
            record.name = record.number

    _sql_constraints = [ ('rfid_card_number_unique', 'unique(number)',
                          'Card numbers must be unique!') ]

    @api.multi
    def unlink(self):
        for card in self:
            card.door_rel_ids.unlink()
        return super(HrRfidCard, self).unlink()

    @api.multi
    def write(self, vals):
        invalid_user_and_contact_msg = 'Card user and contact cannot both be set' \
                                       ' in the same time, and cannot both be empty.'

        for card in self:
            old_number = str(card.number)[:]
            old_owner = card.get_owner()
            old_active = card.card_active
            old_card_type_id = card.card_type.id
            old_cloud = card.cloud_card

            super(HrRfidCard, card).write(vals)

            if len(card.employee_id) > 0 and len(card.contact_id) > 0:
                raise exceptions.ValidationError(invalid_user_and_contact_msg)

            if len(card.employee_id) == 0 and len(card.contact_id) == 0:
                raise exceptions.ValidationError(invalid_user_and_contact_msg)

            if old_number != card.number:
                card.door_rel_ids.card_number_changed(old_number)

            if old_owner != card.get_owner():
                rel_env = self.env['hr.rfid.card.door.rel']
                old_owner_doors = old_owner.get_doors()
                new_owner_doors = card.get_owner().get_doors()
                removed_doors = old_owner_doors - new_owner_doors
                added_doors = new_owner_doors - old_owner_doors
                for door in removed_doors:
                    rel_env.remove_rel(card, door)
                for door in added_doors:
                    rel_env.check_relevance_fast(card, door)

            if old_active != card.card_active:
                card.door_rel_ids.unlink()

            if old_card_type_id != card.card_type.id:
                card.door_rel_ids.card_type_changed()

            if old_cloud != card.cloud_card:
                card.door_rel_ids.cloud_card_changed()

    @api.model_create_multi
    @api.returns('self', lambda value: value.id)
    def create(self, vals):
        card_door_rel_env = self.env['hr.rfid.card.door.rel']
        invalid_user_and_contact_msg = 'Card user and contact cannot both be set' \
                                       ' in the same time, and cannot both be empty.'

        records = self.env['hr.rfid.card']
        for val in vals:
            card = super(HrRfidCard, self).create([val])
            records = records + card

            if len(card.employee_id) > 0 and len(card.contact_id) > 0:
                raise exceptions.ValidationError(invalid_user_and_contact_msg)

            if len(card.employee_id) == 0 and len(card.contact_id) == 0:
                raise exceptions.ValidationError(invalid_user_and_contact_msg)

            card_door_rel_env.create_card_rels(card)

        return records

    @api.model
    def _update_cards(self):
        cenv = self.env['hr.rfid.card']
        now = fields.datetime.now()
        str_before = str(now - timedelta(seconds=31))
        str_after  = str(now + timedelta(seconds=31))
        cards_to_activate = cenv.search([ ('activate_on', '<', str_after),
                                          ('activate_on', '>', str_before) ])
        cards_to_deactivate = cenv.search([ ('deactivate_on', '<', str_after),
                                            ('deactivate_on', '>', str_before) ])

        neutral_cards = cards_to_activate & cards_to_deactivate
        cards_to_activate = cards_to_activate - neutral_cards
        cards_to_deactivate = cards_to_deactivate - neutral_cards

        if len(neutral_cards) > 0:
            to_activate = neutral_cards.filtered(lambda c: c.activate_on >= c.deactivate_on)
            cards_to_activate = cards_to_activate + to_activate
            cards_to_deactivate = cards_to_deactivate + (neutral_cards - to_activate)

        cards_to_activate.write({'card_active': True})
        cards_to_deactivate.write({'card_active': False})


class HrRfidCardType(models.Model):
    _name = 'hr.rfid.card.type'
    _inherit = ['mail.thread']
    _description = 'Card Type'

    name = fields.Char(
        string='Type Name',
        help='Label to differentiate types with',
        required=True,
        track_visibility='onchange',
    )

    card_ids = fields.One2many(
        'hr.rfid.card',
        'card_type',
        string='Cards',
        help='Cards of this card type',
    )

    door_ids = fields.One2many(
        'hr.rfid.door',
        'card_type',
        string='Doors',
        help='Doors that will open to this card type',
    )

    _sql_constraints = [ ('rfid_card_type_unique', 'unique(name)', 'Card types must be unique!') ]

    @api.multi
    def unlink(self):
        default_card_type_id = self.env.ref('hr_rfid.hr_rfid_card_type_def').id

        for card_type in self:
            if card_type.id == default_card_type_id \
                    or len(card_type.card_ids) > 0 \
                    or len(card_type.door_ids) > 0:
                raise exceptions.ValidationError('Cannot delete the default card type or a card '
                                                 'type that is already used by doors or cards. '
                                                 'Please change the doors/cards types first.')

        return super(HrRfidCardType, self).unlink()


class HrRfidCardDoorRel(models.Model):
    _name = 'hr.rfid.card.door.rel'
    _description = 'Card and door relation model'

    card_id = fields.Many2one(
        'hr.rfid.card',
        string='Card',
        required=True,
    )

    door_id = fields.Many2one(
        'hr.rfid.door',
        string='Door',
        required=True,
        ondelete='set null',
    )

    time_schedule_id = fields.Many2one(
        'hr.rfid.time.schedule',
        string='Time Schedule',
        required=True,
        ondelete='set null',
    )

    @api.model
    def create_card_rels(self, card_id: HrRfidCard, access_group: models.Model = None):
        potential_doors = card_id.get_potential_access_doors(access_group)
        for door, ts in potential_doors:
            if self._check_compat_n_rdy(card_id, door):
                self.create_rel(card_id, door, ts)

    @api.model
    def create_door_rels(self, door_id: models.Model, access_group: models.Model = None):
        potential_cards = door_id.get_potential_cards(access_group)
        for card, ts in potential_cards:
            if self._check_compat_n_rdy(card, door_id):
                self.create_rel(card, door_id, ts)

    @api.model
    def check_relevance_slow(self, card_id: HrRfidCard, door_id: models.Model, ts_id: models.Model = None):
        """
        Check if card has access to door. If it does, create relation or do nothing if it exists,
        and if not remove relation or do nothing if it does not exist.
        :param card_id: Recordset containing a single card
        :param door_id: Recordset containing a single door
        :param ts_id: Optional parameter. If supplied, the relation will be created quicker.
        """
        potential_doors = card_id.get_potential_access_doors()
        found_door = False

        for door, ts in potential_doors:
            if door_id == door:
                if ts_id is not None and ts_id != ts:
                    raise exceptions.ValidationError('This should never happen. Please contact the developers. 95328359')
                ts_id = ts
                found_door = True
                break
        if not found_door or not self._check_compat_n_rdy(card_id, door_id):
            self.remove_rel(card_id, door_id)
            return
        self.create_rel(card_id, door_id, ts_id)

    @api.model
    def check_relevance_fast(self, card_id: HrRfidCard, door_id: models.Model, ts_id: models.Model = None):
        """
        Check if card has access to door. If it does, create relation or do nothing if it exists,
        and if not remove relation or do nothing if it does not exist.
        :param card_id: Recordset containing a single card
        :param door_id: Recordset containing a single door
        :param ts_id: Optional parameter. If supplied, the relation will be created quicker.
        """
        if self._check_compat_n_rdy(card_id, door_id):
            self.remove_rel(card_id, door_id)
            return
        self.create_rel(card_id, door_id, ts_id)

    @api.model
    def create_rel(self, card_id: HrRfidCard, door_id: models.Model, ts_id: models.Model = None):
        ret = self.search([
            ('card_id', '=', card_id.id),
            ('door_id', '=', door_id.id),
        ])
        if len(ret) == 0 and self._check_compat_n_rdy(card_id, door_id):
            if ts_id is None:
                acc_grs = card_id.get_owner().mapped('hr_rfid_access_group_ids').mapped('access_group_id')
                acc_gr_door_rel_env = self.env['hr.rfid.access.group.door.rel']
                ret = acc_gr_door_rel_env.search([
                    ('access_group_id', 'in', acc_grs.ids),
                    ('door_id', '=', door_id.id),
                ])
                if len(ret) == 0:
                    raise exceptions.ValidationError('No way this card has access to this door??? 17512849')
                ts_id = ret.time_schedule_id

            self.create([{
                'card_id': card_id.id,
                'door_id': door_id.id,
                'time_schedule_id': ts_id.id,
            }])

    @api.model
    def remove_rel(self, card_id: models.Model, door_id: models.Model):
        ret = self.search([
            ('card_id', '=', card_id.id),
            ('door_id', '=', door_id.id),
        ])
        if len(ret) > 0:
            ret.unlink()

    @api.multi
    def check_rel_relevance(self):
        for rel in self:
            self.check_relevance_slow(rel.card_id, rel.door_id)

    @api.multi
    def cloud_card_changed(self):
        to_unlink = self.env['hr.rfid.card.door.rel']
        for rel in self:
            if rel.card_id.cloud_card is True and rel.door_id.controller_id.external_db is True:
                to_unlink += rel
        to_unlink.unlink()

    @api.multi
    def card_type_changed(self):
        to_unlink = self.env['hr.rfid.card.door.rel']
        for rel in self:
            if rel.card_id.card_type != rel.door_id.card_type:
                to_unlink += rel
        to_unlink.unlink()

    @api.multi
    def time_schedule_changed(self, new_ts):
        self.time_schedule_id = new_ts

    @api.multi
    def pin_code_changed(self):
        self._create_add_card_command()

    @api.multi
    def card_number_changed(self, number):
        self._create_remove_card_command(number)
        self._create_add_card_command()

    @api.multi
    def reload_add_card_command(self):
        self._create_add_card_command()

    @api.model
    def _check_compat_n_rdy(self, card_id, door_id):
        return card_id.door_compatible(door_id) and card_id.card_ready()

    @api.multi
    def _create_add_card_command(self):
        cmd_env = self.env['hr.rfid.command']
        for rel in self:
            door_id = rel.door_id.id
            ts_id = rel.time_schedule_id.id
            pin_code = rel.card_id.pin_code
            card_id = rel.card_id.id
            cmd_env.add_card(door_id, ts_id, pin_code, card_id)

    @api.multi
    def _create_remove_card_command(self, number=None, door_id: int = None):
        cmd_env = self.env['hr.rfid.command']
        for rel in self:
            card_id = rel.card_id.id
            if door_id is None:
                door_id = rel.door_id.id
            if number is None:
                number = card_id.number[:]
            ts_id = rel.time_schedule_id.id
            pin_code = rel.card_id.pin_code
            cmd_env.remove_card(door_id, ts_id, pin_code, number)

    @api.constrains('door_id')
    @api.multi
    def _door_constrains(self):
        for rel in self:
            if len(rel.door_id.access_group_ids) == 0:
                raise exceptions.ValidationError('Door must be part of an access group!')

    @api.model_create_multi
    @api.returns('self', lambda value: value.id)
    def create(self, vals_list):
        records = self.env['hr.rfid.card.door.rel']

        for vals in vals_list:
            rel = super(HrRfidCardDoorRel, self).create([vals])
            records += rel
            rel._create_add_card_command()

        return records

    @api.multi
    def write(self, vals):
        for rel in self:
            old_door = rel.door_id
            old_card = rel.card_id
            old_ts_id = rel.time_schedule_id

            super(HrRfidCardDoorRel, rel).write(vals)

            new_door = rel.door_id
            new_card = rel.card_id
            new_ts_id = rel.time_schedule_id

            if old_door != new_door or old_card != new_card:
                rel._create_remove_card_command(number=old_card.number, door_id=old_door.id)
                rel._create_add_card_command()
            elif old_ts_id != new_ts_id:
                rel._create_add_card_command()

    @api.multi
    def unlink(self):
        for rel in self:
            rel._create_remove_card_command()

        return super(HrRfidCardDoorRel, self).unlink()
