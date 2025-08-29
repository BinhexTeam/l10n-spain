from odoo import fields, models


class AeatSiiMap(models.Model):
    _inherit = "aeat.sii.map"

    active = fields.Boolean("Active", default=True)
