# -*- encoding: utf-8 -*-
#    License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0
from odoo import api, models, exceptions, _
from odoo.modules.registry import RegistryManager
import json
from odoo.tools import ustr


class AccountInvoice(models.Model):
    _inherit = 'account.invoice'

    SII_WDSL_MAPPING = {
        'out_invoice': 'l10n_es_aeat_sii_igic.wsdl_out',
        'out_refund': 'l10n_es_aeat_sii_igic.wsdl_out',
        'in_invoice': 'l10n_es_aeat_sii_igic.wsdl_in',
        'in_refund': 'l10n_es_aeat_sii_igic.wsdl_in',
    }

    def _get_sii_invoice_dict(self):
        inv_dict = super(AccountInvoice, self)._get_sii_invoice_dict()
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
        inv_dict = super(AccountInvoice, self)._get_sii_invoice_dict_in(cancel)
        if self.sii_registration_key.code == "15":
            for p in inv_dict["FacturaRecibida"]["DesgloseFactura"]["DesgloseIGIC"][
                "DetalleIGIC"
            ]:
                p.pop("CuotaSoportada")
        return inv_dict

    @api.multi
    def _get_sii_header(self, tipo_comunicacion=False, cancellation=False):
        header = super(AccountInvoice, self)._get_sii_header(
            tipo_comunicacion, cancellation)
        header['IDVersionSii'] = '1.0'
        return header

    def _iva_to_igic(self, dict):
        dict = json.loads(
            json.dumps(dict).replace("DetalleIVA", "DetalleIGIC"))
        dict = json.loads(
            json.dumps(dict).replace("DesgloseIVA", "DesgloseIGIC"))
        dict = json.loads(
            json.dumps(dict).replace("ImporteTransmisionInmueblesSujetoAIVA",
                                     "ImporteTransmisionInmueblesSujetoAIGIC"))
        dict = json.loads(
            json.dumps(dict).replace("PeriodoImpositivo",
                                     "PeriodoLiquidacion"))
        return dict

    @api.multi
    def _send_invoice_to_sii(self):
        for invoice in self.filtered(lambda i: i.state in ['open', 'paid']):
            serv = invoice._connect_sii(invoice.type)
            if invoice.sii_state == 'not_sent':
                tipo_comunicacion = 'A0'
            else:
                tipo_comunicacion = 'A1'
            header = invoice._get_sii_header(tipo_comunicacion)
            inv_vals = {
                'sii_header_sent': json.dumps(header, indent=4),
            }
            try:
                inv_dict = invoice._get_sii_invoice_dict()
                inv_dict = self._iva_to_igic(inv_dict)

                inv_vals['sii_content_sent'] = json.dumps(inv_dict, indent=4)
                if invoice.type in ['out_invoice', 'out_refund']:
                    res = serv.SuministroLRFacturasEmitidas(header, inv_dict)
                elif invoice.type in ['in_invoice', 'in_refund']:
                    #Compras comerciante minorista
                    if self.sii_registration_key.code == '15' and self.sii_registration_key.type == 'purchase':
                        for p in inv_dict['FacturaRecibida'][
                                'DesgloseFactura']['DesgloseIGIC'][
                                    'DetalleIGIC']:
                            p.pop('CuotaSoportada')
                    res = serv.SuministroLRFacturasRecibidas(header, inv_dict)
                # TODO Facturas intracomunitarias 66 RIVA
                # elif invoice.fiscal_position.id == self.env.ref(
                #     'account.fp_intra').id:
                #     res = serv.SuministroLRDetOperacionIntracomunitaria(
                #         header, invoices)
                res_line = res['RespuestaLinea'][0]
                if res['EstadoEnvio'] == 'Correcto':
                    inv_vals.update({
                        'sii_state': 'sent',
                        'sii_csv': res['CSV'],
                        'sii_send_failed': False,
                    })
                elif res['EstadoEnvio'] == 'ParcialmenteCorrecto' and \
                        res_line['EstadoRegistro'] == 'AceptadoConErrores':
                    inv_vals.update({
                        'sii_state': 'sent_w_errors',
                        'sii_csv': res['CSV'],
                        'sii_send_failed': True,
                    })
                else:
                    inv_vals['sii_send_failed'] = True
                if ('sii_state' in inv_vals
                        and not invoice.sii_account_registration_date
                        and invoice.type[:2] == 'in'):
                    inv_vals['sii_account_registration_date'] = (
                        self._get_account_registration_date())
                inv_vals['sii_return'] = res
                send_error = False
                if res_line['CodigoErrorRegistro']:
                    send_error = u"{} | {}".format(
                        unicode(res_line['CodigoErrorRegistro']),
                        unicode(res_line['DescripcionErrorRegistro'])[:60])
                inv_vals['sii_send_error'] = send_error
                invoice.write(inv_vals)
            except Exception as fault:
                new_cr = RegistryManager.get(self.env.cr.dbname).cursor()
                env = api.Environment(new_cr, self.env.uid, self.env.context)
                invoice = env['account.invoice'].browse(self.id)
                inv_vals.update({
                    'sii_send_failed': True,
                    'sii_send_error': ustr(fault),
                    'sii_return': ustr(fault),
                })
                invoice.write(inv_vals)
                new_cr.commit()
                new_cr.close()
                raise
