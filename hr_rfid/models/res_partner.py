# -*- coding: utf-8 -*-
from odoo import api, fields, models, exceptions


class ResPartner(models.Model):
    _inherit = 'res.partner'

    hr_rfid_pin_code = fields.Char(
        string='Contact pin code',
        help="Pin code for this contact, four zeroes means that the contact has no pin code.",
        limit=4,
        default='0000',
        track_visibility='onchange',
    )

    hr_rfid_access_group_ids = fields.One2many(
        'hr.rfid.access.group.contact.rel',
        'contact_id',
        string='Access Group',
        help='Which access group the contact is a part of',
        track_visibility='onchange',
    )

    hr_rfid_card_ids = fields.One2many(
        'hr.rfid.card',
        'contact_id',
        string='RFID Card',
        help='Cards owned by the contact',
    )

    hr_rfid_event_ids = fields.One2many(
        'hr.rfid.event.user',
        'contact_id',
        string='RFID Events',
        help='Events concerning this contact',
    )

    @api.multi
    def add_acc_gr(self, access_group, expiration=None):
        access_group.ensure_one()
        rel_env = self.env['hr.rfid.access.group.contact.rel']
        for contact in self:
            contact.check_for_ts_inconsistencies_when_adding(access_group)
            creation_dict = {
                'contact_id': contact.id,
                'access_group_id': access_group.id,
            }
            if expiration is not None and expiration is not False:
                creation_dict['expiration'] = str(expiration)
            rel_env.create(creation_dict)

    @api.multi
    def remove_acc_gr(self, access_group):
        rel_env = self.env['hr.rfid.access.group.contact.rel']
        for contact in self:
            rel_env.search([
                ('contact_id', '=', contact.id),
                ('access_group_id', '=', access_group.id)
            ]).unlink()

    @api.one
    @api.returns('hr.rfid.door')
    def get_doors(self, excluding_acc_grs=None, including_acc_grs=None):
        if excluding_acc_grs is None:
            excluding_acc_grs = self.env['hr.rfid.access.group']
        if including_acc_grs is None:
            including_acc_grs = self.env['hr.rfid.access.group']

        acc_grs = self.hr_rfid_access_group_ids.mapped('access_group_id')
        acc_grs = acc_grs - excluding_acc_grs
        acc_grs = acc_grs + including_acc_grs
        return acc_grs.mapped('all_door_ids').mapped('door_id')

    @api.one
    def check_for_ts_inconsistencies_when_adding(self, new_acc_grs):
        acc_gr_door_rel_env = self.env['hr.rfid.access.group.door.rel']
        acc_grs = self.hr_rfid_access_group_ids.mapped('access_group_id')
        for acc_gr1 in acc_grs:
            for acc_gr2 in new_acc_grs:
                door_rels1 = acc_gr1.all_door_ids
                door_rels2 = acc_gr2.all_door_ids
                acc_gr_door_rel_env.check_for_ts_inconsistencies(door_rels1, door_rels2)

    @api.one
    def check_for_ts_inconsistencies(self):
        acc_gr_door_rel_env = self.env['hr.rfid.access.group.door.rel']
        acc_grs = self.hr_rfid_access_group_ids.mapped('access_group_id')
        for i in range(len(acc_grs)):
            for j in range(i+1, len(acc_grs)):
                door_rels1 = acc_grs[i].all_door_ids
                door_rels2 = acc_grs[i].all_door_ids
                acc_gr_door_rel_env.check_for_ts_inconsistencies(door_rels1, door_rels2)

    @api.multi
    @api.constrains('hr_rfid_access_group_ids')
    def _check_access_group(self):
        for user in self:
            user.check_for_ts_inconsistencies()

    @api.multi
    @api.constrains('hr_rfid_pin_code')
    def _check_pin_code(self):
        for contact in self:
            pin = contact.hr_rfid_pin_code
            if len(pin) != 4:
                raise exceptions.ValidationError('Pin code must have exactly 4 characters')

            # If char is not a valid hex number, int(char, 16) will raise an error
            try:
                for char in str(pin):
                    int(char, 10)
            except ValueError:
                raise exceptions.ValidationError('Invalid pin code, digits must be from 0 to 9')

    @api.multi
    def write(self, vals):
        for user in self:
            old_pin_code = user.hr_rfid_pin_code[:]
            super(ResPartner, user).write(vals)

            if old_pin_code != user.hr_rfid_pin_code:
                user.hr_rfid_card_ids.mapped('door_rel_ids').pin_code_changed()

    @api.multi
    def unlink(self):
        for emp in self:
            emp.hr_rfid_card_ids.unlink()
        return super(ResPartner, self).unlink()


class ResPartnerAccGrWizard(models.TransientModel):
    _name = 'res.partner.acc.grs'
    _description = 'Add or remove access groups to or from the contact'

    def _default_user(self):
        return self.env['res.partner'].browse(self._context.get('active_ids'))

    def _default_allowed_acc_grs(self):
        contact = self._default_user()
        cur_acc_grs = contact.hr_rfid_access_group_ids.mapped('access_group_id')
        if self._context.get('adding_acc_grs', False):
            return self.env['hr.rfid.access.group'].search([]) - cur_acc_grs
        return cur_acc_grs

    contact_id = fields.Many2one(
        'res.partner',
        string='Contact',
        required=True,
        default=_default_user,
    )

    allowed_acc_grs = fields.Many2many(
        'hr.rfid.access.group',
        'res_partner_acc_grs_allowed_grs_rel',
        default=_default_allowed_acc_grs,
    )

    acc_gr_ids = fields.Many2many(
        'hr.rfid.access.group',
        'res_partner_acc_grs_acc_gr_ids_rel',
        string='Access groups to add/remove',
        required=True,
    )

    expiration = fields.Datetime(
        string='Expiration',
        help='When the access groups will be removed from the contact.',
    )

    @api.multi
    def add_acc_grs(self):
        self.ensure_one()
        rel_env = self.env['hr.rfid.access.group.contact.rel']

        acc_gr_ids = rel_env.search([
            ('contact_id', '=', self.contact_id.id),
            ('access_group_id', 'in', self.acc_gr_ids.ids),
        ]).mapped('access_group_id')

        acc_gr_ids = self.acc_gr_ids - acc_gr_ids

        for acc_gr_id in acc_gr_ids:
            self.contact_id.add_acc_gr(acc_gr_id, self.expiration)

    @api.multi
    def rem_acc_grs(self):
        self.ensure_one()
        rel_env = self.env['hr.rfid.access.group.contact.rel']

        rel_env.search([
            ('contact_id', '=', self.contact_id.id),
            ('access_group_id', 'in', self.acc_gr_ids.ids),
        ]).unlink()
