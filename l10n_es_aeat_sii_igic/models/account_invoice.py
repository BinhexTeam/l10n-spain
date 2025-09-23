import json

from odoo import models


class AccountInvoice(models.Model):
    _inherit = 'account.invoice'

    def _get_sii_invoice_dict(self):
        inv_dict = super()._get_sii_invoice_dict()
        inv_dict = json.loads(json.dumps(inv_dict).replace("DetalleIVA", "DetalleIGIC"))
        inv_dict = json.loads(
            json.dumps(inv_dict).replace("DesgloseIVA", "DesgloseIGIC")
        )
        inv_dict = json.loads(
            json.dumps(inv_dict).replace(
                "ImporteTransmisionInmueblesSujetoAIVA",
                "ImporteTransmisionInmueblesSujetoAIGIC",
            )
        )
        inv_dict = json.loads(
            json.dumps(inv_dict).replace("PeriodoImpositivo", "PeriodoLiquidacion")
        )
        return inv_dict

    def _get_sii_invoice_dict_in(self, cancel=False):
        inv_dict = super()._get_sii_invoice_dict_in(cancel)
        if self.sii_registration_key.code == "15":
            for p in inv_dict["FacturaRecibida"]["DesgloseFactura"]["DesgloseIGIC"][
                "DetalleIGIC"
            ]:
                p.pop("CuotaSoportada")
        return inv_dict
