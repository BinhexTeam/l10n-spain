# Copyright 2017 Ignacio Ibeas <ignacio@acysos.com>
# Copyright 2017 Studio73 - Pablo Fuentes <pablo@studio73>
# Copyright 2017 Studio73 - Jordi Tolsà <jordi@studio73.es>
# Copyright 2018 Javi Melendez <javimelex@gmail.com>
# Copyright 2018 PESOL - Angel Moya <angel.moya@pesol.es>
# Copyright 2020 Valentin Vinagre <valent.vinagre@sygel.es>
# Copyright 2021 Tecnativa - João Marques
# Copyright 2022 ForgeFlow - Lois Rilo
# Copyright 2011-2023 Tecnativa - Pedro M. Baeza
# Copyright 2023 Aures Tic - Almudena de la Puente <almudena@aurestic.es>
# Copyright 2023 Aures Tic - Jose Zambudio <jose@aurestic.es>
# Copyright 2023 Moduon Team - Eduardo de Miguel
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import json
import logging

from unidecode import unidecode

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv.expression import AND, OR
from odoo.tools import format_date

SII_VALID_INVOICE_STATES = ["posted"]
SII_MONTHLY_PERIOD_SELECTION = [
    ("%02d" % month, "%02d" % month) for month in range(1, 13)
]
SII_QUARTERLY_PERIOD_SELECTION = [
    ("1T", "1T"),
    ("2T", "2T"),
    ("3T", "3T"),
    ("4T", "4T"),
]
SII_DEDUCTION_PERIOD_SELECTION = [
    *SII_MONTHLY_PERIOD_SELECTION,
    *SII_QUARTERLY_PERIOD_SELECTION,
]


def _get_sii_deduction_year_selection(record=None):
    """Years that can appear in EjercicioDeduccion.

    The form narrows this list from the liquidation year, or the next
    year when that year is closed, through four years after the invoice
    date. The selection itself stays wide enough for older invoices.
    """
    current_year = fields.Date.today().year
    return [(str(year), str(year)) for year in range(2017, current_year + 9)]


_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "sii.mixin"]

    def _get_default_type(self):
        context = self.env.context
        return context.get("move_type", context.get("default_move_type"))

    def _default_sii_refund_type(self):
        inv_type = self._get_default_type()
        return "I" if inv_type in ["out_refund", "in_refund"] else False

    sii_refund_specific_invoice_type = fields.Selection(
        selection=[
            ("R1", "Error based on law and Art. 80 One and Two LIVA (R1)"),
            ("R2", "Art. 80 Three LIVA - Bankruptcy (R2)"),
            ("R3", "Art. 80 Four LIVA - Bad debt (R3)"),
            ("R4", "Rest of causes (R4)"),
            ("R5", "Simplified invoice (R5)"),
        ],
        help="TipoFactura of a refund. On a supplier refund, leave it empty "
        "to send R4. A substitution with a rectified base or quota also "
        "sends the original invoice number and date.",
    )
    sii_registration_key_additional1 = fields.Many2one(
        comodel_name="aeat.sii.mapping.registration.keys",
        string="Additional SII registration key",
    )
    sii_registration_key_additional2 = fields.Many2one(
        comodel_name="aeat.sii.mapping.registration.keys",
        string="Additional 2 SII registration key",
    )
    sii_property_location = fields.Selection(
        string="Real property location",
        copy=False,
        selection=[
            (
                "1",
                "[1]-Real property with cadastral code located within "
                "the Spanish territory except Basque Country or Navarra",
            ),
            ("2", "[2]-Real property located in the Basque Country or Navarra"),
            (
                "3",
                "[3]-Real property in any of the above situations "
                "but without cadastral code",
            ),
            ("4", "[4]-Real property located in a foreign country"),
        ],
    )
    sii_property_cadastrial_code = fields.Char(
        string="Real property cadastrial code",
        copy=False,
    )
    sii_lc_operation = fields.Boolean(
        string="Customs - Complementary settlement",
        help="Check this mark if this invoice represents a complementary "
        "settlement for customs.\n"
        "The invoice number should start with LC, QZC, QRC, A01 or A02.",
        copy=False,
    )
    sii_is_accounting_receipt = fields.Boolean(string="Is accounting receipt?")
    sii_dua_invoice = fields.Boolean(compute="_compute_dua_invoice")
    sii_send_date_visible = fields.Boolean(compute="_compute_sii_send_date_visible")
    sii_deducir_en_periodo_posterior = fields.Boolean(
        string="Deduct in a later period",
        copy=False,
        help="Sends ADeducirEnPeriodoPosterior as S. "
        "The deduction year and period are then required. "
        "Leave it unchecked to omit the three nodes.",
    )
    sii_ejercicio_deduccion = fields.Selection(
        selection=_get_sii_deduction_year_selection,
        string="Deduction year",
        copy=False,
        help="EjercicioDeduccion, four digits (YYYY). "
        "From the liquidation year, or the following year when that "
        "year has no later period, through four years from the "
        "accrual/invoice date.",
    )
    sii_periodo_deduccion = fields.Selection(
        selection=SII_DEDUCTION_PERIOD_SELECTION,
        string="Deduction period",
        copy=False,
        help="PeriodoDeduccion. Use 01-12 when the company files monthly "
        "and 1T-4T when it files quarterly. "
        "The year and period together must be strictly later than the "
        "liquidation period: the month or quarter has to be greater "
        "only in the same year. The annual value 0A is not accepted.",
    )
    sii_deduction_period_bounds = fields.Json(
        compute="_compute_sii_deduction_period_bounds",
    )

    @api.depends(
        "move_type",
        "invoice_date",
        "company_id.sii_period",
        "sii_account_registration_date",
        "aeat_state",
        "aeat_content_sent",
    )
    def _compute_sii_deduction_period_bounds(self):
        for move in self:
            move.sii_deduction_period_bounds = (
                move._prepare_sii_deduction_period_bounds()
            )

    @api.onchange("sii_deducir_en_periodo_posterior")
    def _onchange_sii_deducir_en_periodo_posterior(self):
        if self.move_type not in ("in_invoice", "in_refund"):
            return
        if (
            self.sii_deducir_en_periodo_posterior
            and self._is_sii_deduction_choice_allowed(
                self.sii_ejercicio_deduccion, self.sii_periodo_deduccion
            )
        ):
            return
        self.sii_ejercicio_deduccion = False
        self.sii_periodo_deduccion = False

    @api.onchange("sii_ejercicio_deduccion")
    def _onchange_sii_ejercicio_deduccion(self):
        if (
            self.move_type not in ("in_invoice", "in_refund")
            or not self.sii_deducir_en_periodo_posterior
            or self._is_sii_deduction_choice_allowed(
                self.sii_ejercicio_deduccion, self.sii_periodo_deduccion
            )
        ):
            return
        self.sii_periodo_deduccion = False

    @api.onchange("invoice_date")
    def _onchange_sii_deduction_invoice_date(self):
        """Drop a deduction choice the invoice date no longer allows.

        ATC counts the four-year term from the accrual. This module sends
        that date as FechaExpedicionFacturaEmisor (invoice_date).
        """
        if self.move_type not in ("in_invoice", "in_refund"):
            return
        if not self.sii_ejercicio_deduccion and not self.sii_periodo_deduccion:
            return
        if self._is_sii_deduction_choice_allowed(
            self.sii_ejercicio_deduccion, self.sii_periodo_deduccion
        ):
            return
        bounds = self._prepare_sii_deduction_period_bounds() or {}
        if str(self.sii_ejercicio_deduccion or "") not in (bounds.get("years") or []):
            self.sii_ejercicio_deduccion = False
        self.sii_periodo_deduccion = False

    @api.constrains(
        "move_type",
        "invoice_date",
        "sii_deducir_en_periodo_posterior",
        "sii_ejercicio_deduccion",
        "sii_periodo_deduccion",
    )
    def _check_sii_deduction_period(self):
        for move in self.filtered(
            lambda record: record.move_type in ("in_invoice", "in_refund")
        ):
            has_year = bool(move.sii_ejercicio_deduccion)
            has_period = bool(move.sii_periodo_deduccion)
            if has_year != has_period:
                raise ValidationError(
                    self.env._(
                        "The SII deduction year and period must be filled together."
                    )
                )
            if not has_year:
                if move.sii_deducir_en_periodo_posterior:
                    raise ValidationError(
                        self.env._(
                            "A later deduction requires the deduction year "
                            "and period."
                        )
                    )
                continue
            if not move.sii_deducir_en_periodo_posterior:
                raise ValidationError(
                    self.env._(
                        "The later deduction checkbox must be checked when "
                        "a deduction year and period are set."
                    )
                )
            if (
                move.company_id.sii_period == "monthly"
                and not move.sii_periodo_deduccion.isdigit()
            ) or (
                move.company_id.sii_period == "quarterly"
                and not move.sii_periodo_deduccion.endswith("T")
            ):
                raise ValidationError(
                    self.env._(
                        "The SII deduction period must use the company's "
                        "monthly or quarterly periodicity."
                    )
                )
            liquidation_year, liquidation_period = (
                move._get_sii_incoming_liquidation_period()
            )
            if move._sii_period_key(
                int(move.sii_ejercicio_deduccion),
                move.sii_periodo_deduccion,
            ) <= move._sii_period_key(liquidation_year, liquidation_period):
                raise ValidationError(
                    self.env._(
                        "The SII deduction period must be later than the "
                        "liquidation period."
                    )
                )
            limit_year = move._get_sii_deduction_year_limit()
            if limit_year is None or int(move.sii_ejercicio_deduccion) > limit_year:
                raise ValidationError(
                    self.env._(
                        "The SII deduction period exceeds the four-year term "
                        "counted from the invoice date."
                    )
                )

    @staticmethod
    def _sii_period_key(year, period):
        if period == "0A":
            month = 12
        elif period.endswith("T"):
            month = int(period[0]) * 3
        else:
            month = int(period)
        return int(year), month

    def _get_sii_company_period_codes(self):
        self.ensure_one()
        if self.company_id.sii_period == "quarterly":
            selection = SII_QUARTERLY_PERIOD_SELECTION
        else:
            selection = SII_MONTHLY_PERIOD_SELECTION
        return [key for key, __ in selection]

    def _get_sii_deduction_year_limit(self):
        """Last EjercicioDeduccion allowed by the four-year accrual term.

        ATC FAQ 4.1 counts that term from the accrual. The sent accrual is
        the invoice date (FechaExpedicionFacturaEmisor).
        """
        self.ensure_one()
        accrual = fields.Date.to_date(self.invoice_date)
        if not accrual:
            return None
        return accrual.year + 4

    def _prepare_sii_deduction_period_bounds(self):
        """Years and periods the form may offer for a later deduction.

        Same-year periods are only those strictly after the liquidation
        period. A closed year, such as December or 4T, starts at the
        next year. The last year is four years after the invoice date.
        """
        self.ensure_one()
        if self.move_type not in ("in_invoice", "in_refund"):
            return False
        liquidation_year, liquidation_period = (
            self._get_sii_incoming_liquidation_period()
        )
        liquidation_year = int(liquidation_year)
        company_periods = self._get_sii_company_period_codes()
        same_year_periods = [
            period
            for period in company_periods
            if self._sii_period_key(liquidation_year, period)
            > self._sii_period_key(liquidation_year, liquidation_period)
        ]
        limit_year = self._get_sii_deduction_year_limit()
        first_year = liquidation_year if same_year_periods else liquidation_year + 1
        if limit_year is None or limit_year < first_year:
            years = []
        else:
            years = [str(year) for year in range(first_year, limit_year + 1)]
        return {
            "liquidation_year": str(liquidation_year),
            "years": years,
            "periods_same_year": same_year_periods,
            "periods_other_year": company_periods,
        }

    def _is_sii_deduction_choice_allowed(self, year, period):
        self.ensure_one()
        bounds = self._prepare_sii_deduction_period_bounds()
        if not bounds or not year or not period:
            return False
        if str(year) not in bounds["years"]:
            return False
        if str(year) == bounds["liquidation_year"]:
            return period in bounds["periods_same_year"]
        return period in bounds["periods_other_year"]

    def _get_sii_period_from_date(self, date):
        self.ensure_one()
        month = fields.Date.to_date(date).month
        if self.company_id.sii_period == "monthly":
            return "%02d" % month
        return str(int(((month - 1) / 3) + 1)) + "T"

    def _get_sii_sent_period(self):
        self.ensure_one()
        if not self.aeat_content_sent:
            return None
        try:
            payload = json.loads(self.aeat_content_sent)
        except (TypeError, ValueError):
            return None
        return payload.get("PeriodoLiquidacion")

    def _get_sii_incoming_liquidation_period(self, cancel=False):
        self.ensure_one()
        if cancel or self.aeat_state in {
            "sent",
            "sent_w_errors",
            "sent_modified",
            "cancelled",
            "cancelled_modified",
        }:
            sent_period = self._get_sii_sent_period()
            if sent_period:
                return sent_period["Ejercicio"], sent_period["Periodo"]
        registration_date = fields.Date.to_date(self._get_account_registration_date())
        return registration_date.year, self._get_sii_period_from_date(registration_date)

    def _get_sii_deduction_period_values(self):
        """Later-deduction nodes follow the form, also on an A1.

        Empty year and period mean the invoice is not deferred. The
        previous ``aeat_content_sent`` is not copied back: an A1 replaces
        ``FacturaRecibida``, and omitting the nodes removes the deferral.
        ``PeriodoLiquidacion`` stays the one already sent.
        """
        self.ensure_one()
        if not self.sii_ejercicio_deduccion or not self.sii_periodo_deduccion:
            return {}
        return {
            "ADeducirEnPeriodoPosterior": (
                "S" if self.sii_deducir_en_periodo_posterior else "N"
            ),
            "EjercicioDeduccion": self.sii_ejercicio_deduccion,
            "PeriodoDeduccion": self.sii_periodo_deduccion,
        }

    @api.depends("move_type")
    def _compute_sii_refund_type(self):
        for record in self:
            if "refund" not in (record.move_type or ""):
                record.sii_refund_type = False
            elif not record.sii_refund_type:
                record.sii_refund_type = "I"

    @api.depends("move_type")
    def _compute_sii_registration_key_domain(self):
        return super()._compute_sii_registration_key_domain()

    @api.depends("move_type")
    def _compute_sii_registration_key(self):
        return super()._compute_sii_registration_key()

    @api.depends("amount_total")
    def _compute_macrodata(self):
        return super()._compute_macrodata()

    @api.depends("company_id", "fiscal_position_id", "invoice_line_ids.tax_ids")
    def _compute_dua_invoice(self):
        for invoice in self:
            xmlids = [
                "account_tax_template_p_iva4_ibc_group",
                "account_tax_template_p_iva10_ibc_group",
                "account_tax_template_p_iva21_ibc_group",
            ]
            taxes = invoice.company_id._get_taxes_from_xmlids(xmlids)
            invoice.sii_dua_invoice = invoice.line_ids.filtered(
                lambda x, taxes=taxes: bool(taxes & x.tax_ids)
            )

    @api.depends("state", "aeat_state", "sii_needs_cancel")
    def _compute_sii_send_date_visible(self):
        for invoice in self:
            invoice.sii_send_date_visible = (
                invoice.state == "posted"
                and invoice.aeat_state not in ["sent", "cancelled"]
            ) or (
                invoice.state == "cancel"
                and invoice.sii_needs_cancel
                and invoice.aeat_state in ["sent", "sent_w_errors", "sent_modified"]
            )

    def _aeat_get_partner(self):
        return self.commercial_partner_id

    def _raise_exception_sii(self, field_name):
        raise UserError(
            self.env._(
                "You cannot change the %s of an invoice "
                "already registered at the SII. You must cancel the "
                "invoice and create a new one with the correct value"
            )
            % field_name
        )

    def write(self, vals):
        """For supplier invoices the SII primary key is the supplier
        VAT/ID Otro and the supplier invoice number. Cannot let change these
        values in a SII registered supplier invoice.

        Deduction dates are not part of that key. Changing them on an
        already registered purchase queues a modification (A1). The
        original liquidation period stays. A tax period already closed
        by the company's tax lock date cannot be changed.
        """
        for invoice in self.filtered(
            lambda x: x.is_invoice() and x.aeat_state != "not_sent"
        ):
            if "invoice_date" in vals:
                self._raise_exception_sii(self.env._("invoice date"))
            elif "thirdparty_number" in vals:
                self._raise_exception_sii(self.env._("third-party number"))
            if invoice.move_type in ["in_invoice", "in_refund"]:
                if "partner_id" in vals:
                    correct_partners = invoice._aeat_get_partner()
                    correct_partners |= correct_partners.child_ids
                    if vals["partner_id"] not in correct_partners.ids:
                        self._raise_exception_sii(self.env._("supplier"))
                elif "ref" in vals:
                    self._raise_exception_sii(self.env._("supplier invoice number"))
            elif invoice.move_type in ["out_invoice", "out_refund"]:
                if "name" in vals:
                    self._raise_exception_sii(self.env._("invoice number"))
        previous_deduction = self._sii_deduction_before_write(vals)
        res = super().write(vals)
        self._sii_deduction_after_write(previous_deduction)
        return res

    def _sii_deduction_fields(self):
        return (
            "sii_deducir_en_periodo_posterior",
            "sii_ejercicio_deduccion",
            "sii_periodo_deduccion",
        )

    def _sii_deduction_values(self, vals=None):
        fields_to_read = self._sii_deduction_fields()
        return tuple(
            (vals[field] if vals and field in vals else self[field])
            for field in fields_to_read
        )

    def _sii_deduction_before_write(self, vals):
        """Remember the deduction and refuse a change inside a closed period.

        An empty dict means this write does not track the deduction.
        """
        deduction_fields = self._sii_deduction_fields()
        if not (set(deduction_fields) & set(vals)) or "aeat_state" in vals:
            return {}
        previous = {invoice.id: invoice._sii_deduction_values() for invoice in self}
        for invoice in self:
            if invoice._sii_deduction_values(vals) == previous[invoice.id]:
                continue
            if invoice.move_type not in ("in_invoice", "in_refund"):
                continue
            if invoice.aeat_state in ("not_sent", False) and invoice.state != "posted":
                continue
            invoice._check_sii_deduction_tax_lock()
        return previous

    def _sii_deduction_after_write(self, previous_deduction):
        """Queue a SII modification when a sent purchase changes deduction."""
        if not previous_deduction:
            return
        changed = self.filtered(
            lambda invoice: invoice.move_type in ("in_invoice", "in_refund")
            and invoice.aeat_state in ("sent", "sent_w_errors")
            and previous_deduction[invoice.id] != invoice._sii_deduction_values()
        )
        if changed:
            super(AccountMove, changed).write({"aeat_state": "sent_modified"})

    def _get_sii_deduction_tax_date(self):
        """Date of the period already declared for this purchase.

        The SII registration date is the liquidation month. Until the
        invoice is registered, the accounting date is the one a filed
        return would have used.
        """
        self.ensure_one()
        return fields.Date.to_date(self.sii_account_registration_date or self.date)

    def _check_sii_deduction_tax_lock(self):
        """Refuse a deduction change inside a closed tax period.

        ``tax_lock_date`` is set when the tax return is posted. A user
        lock exception, the same one Odoo uses for the tax lock, is the
        only way through.
        """
        self.ensure_one()
        tax_date = self._get_sii_deduction_tax_date()
        violations = self.company_id._get_lock_date_violations(
            tax_date,
            fiscalyear=False,
            sale=False,
            purchase=False,
            tax=True,
            hard=True,
        )
        if not violations:
            return
        raise UserError(
            self.env._(
                "The deduction period cannot be changed. The registration "
                "date %(date)s belongs to a tax period that is already "
                "closed (%(locks)s). Changing it would remove the quota "
                "from the return already filed and deduct it again later.",
                date=format_date(self.env, tax_date) if tax_date else "",
                locks=self.env["res.company"]._format_lock_dates(violations),
            )
        )

    def _filter_sii_unlink_not_possible(self):
        """Filter records that we can delete to apply only to invoices."""
        res = super()._filter_sii_unlink_not_possible()
        return res.filtered(lambda x: x.is_invoice())

    def _get_sii_tax_req(self, tax):
        """Get the associated req tax for the specified tax.

        :param self: Single invoice record.
        :param tax: Initial tax for searching for the RE linked tax.
        :return: REQ tax (or empty recordset) linked to the provided tax.
        """
        self.ensure_one()
        taxes_req = self._get_aeat_taxes_map(["RE"], self._get_document_fiscal_date())
        re_lines = self.line_ids.filtered(
            lambda x: tax in x.tax_ids and x.tax_ids & taxes_req
        )
        req_tax = re_lines.mapped("tax_ids") & taxes_req
        if len(req_tax) > 1:
            raise UserError(
                self.env._("There's a mismatch in taxes for RE. Check them.")
            )
        return req_tax

    @api.model
    def _get_sii_tax_dict(self, tax_line, tax_lines):
        """Get the SII tax dictionary for the passed tax line.

        :param self: Single invoice record.
        :param tax_line: Tax line that is being analyzed.
        :param tax_lines: Dictionary of processed invoice taxes for further operations
            (like REQ).
        :return: A dictionary with the corresponding SII tax values.
        """
        tax = tax_line["tax"]
        tax_base_amount = tax_line["base"]
        if tax.amount_type == "group":
            tax_type = abs(tax.children_tax_ids.filtered("amount")[:1].amount)
        else:
            tax_type = abs(tax.amount)
        tax_dict = {"TipoImpositivo": str(tax_type), "BaseImponible": tax_base_amount}
        if self.move_type in ["out_invoice", "out_refund"]:
            key = "CuotaRepercutida"
        else:
            key = "CuotaSoportada"
        tax_dict[key] = tax_line["amount"]
        # Recargo de equivalencia
        req_tax = self._get_sii_tax_req(tax)
        if req_tax:
            tax_dict["TipoRecargoEquivalencia"] = req_tax.amount
            tax_dict["CuotaRecargoEquivalencia"] = tax_lines[req_tax]["amount"]
        return tax_dict

    def _get_document_amount_total(self):
        return self.amount_total_signed

    def _get_tax_info(self):
        # Use the method at l10n_es_aeat that returns the needed info
        return self._get_aeat_tax_info()

    def _get_sii_in_taxes(self):
        """Get the taxes for purchase invoices.

        :param self:  Single invoice record.
        """
        self.ensure_one()
        taxes_dict = {}
        taxes_sfrs = self._get_aeat_taxes_map(["SFRS"], self.date)
        taxes_sfrsa = self._get_aeat_taxes_map(["SFRSA"], self.date)
        taxes_sfrisp = self._get_aeat_taxes_map(["SFRISP"], self.date)
        taxes_sfrns = self._get_aeat_taxes_map(["SFRNS"], self.date)
        taxes_sfrnd = self._get_aeat_taxes_map(["SFRND"], self.date)
        taxes_sfrbi = self._get_aeat_taxes_map(["SFRBI"], self.date)
        taxes_not_in_total = self._get_aeat_taxes_map(["NotIncludedInTotal"], self.date)
        taxes_not_in_total_neg = self._get_aeat_taxes_map(
            ["NotIncludedInTotalNegative"], self.date
        )
        base_not_in_total = self._get_aeat_taxes_map(
            ["BaseNotIncludedInTotal"], self.date
        )
        tax_amount = 0.0
        not_in_amount_total = 0.0
        tax_lines = self._get_aeat_tax_info()
        for tax_line in tax_lines.values():
            tax = tax_line["tax"]
            if tax in taxes_not_in_total:
                not_in_amount_total += tax_line["amount"]
            elif tax in taxes_not_in_total_neg:
                not_in_amount_total -= tax_line["amount"]
            elif tax in base_not_in_total:
                not_in_amount_total += tax_line["base"]
            if tax in taxes_sfrisp:
                base_dict = taxes_dict.setdefault(
                    "InversionSujetoPasivo",
                    {"DetalleIVA": []},
                )
            elif tax in taxes_sfrs + taxes_sfrns + taxes_sfrsa + taxes_sfrnd:
                base_dict = taxes_dict.setdefault("DesgloseIVA", {"DetalleIVA": []})
            else:
                continue
            tax_dict = self._get_sii_tax_dict(tax_line, tax_lines)
            if tax in taxes_sfrisp + taxes_sfrs + taxes_sfrsa:
                tax_amount += tax_line["deductible_amount"]
            if tax in taxes_sfrbi:
                tax_dict["BienInversion"] = "S"
            if tax in taxes_sfrns:
                tax_dict.pop("TipoImpositivo")
                tax_dict.pop("CuotaSoportada")
                base_dict["DetalleIVA"].append(tax_dict)
            elif tax in taxes_sfrsa:
                tax_dict["PorcentCompensacionREAGYP"] = tax_dict.pop("TipoImpositivo")
                tax_dict["ImporteCompensacionREAGYP"] = tax_dict.pop("CuotaSoportada")
                base_dict["DetalleIVA"].append(tax_dict)
            else:
                if not self._merge_tax_dict(
                    base_dict["DetalleIVA"],
                    tax_dict,
                    ["TipoImpositivo", "BienInversion"],
                    ["BaseImponible", "CuotaSoportada"],
                ):
                    base_dict["DetalleIVA"].append(tax_dict)
        return taxes_dict, tax_amount, not_in_amount_total

    def _get_mapping_key(self):
        return self.move_type

    def _aeat_check_exceptions(self):
        res = super()._aeat_check_exceptions()
        is_simplified_invoice = self._is_aeat_simplified_invoice()
        if is_simplified_invoice and self.move_type[:2] == "in":
            error_msg = self.env._("You can't make a supplier simplified invoice.")
            raise UserError(error_msg)
        if not self.ref and self.move_type in ["in_invoice", "in_refund"]:
            error_msg = self.env._("The supplier number invoice is required")
            raise UserError(error_msg)
        if self.sii_refund_type == "S" and self.move_type in (
            "in_refund",
            "out_refund",
        ):
            self._check_sii_substitution_refund()
        return res

    def _get_sii_rectified_serial_number(self, origin):
        """Supplier serial is the vendor reference. Customer serial is the name."""
        self.ensure_one()
        if self.move_type in ("in_invoice", "in_refund"):
            return (origin.ref or "")[:60]
        return origin._get_document_serial_number()

    def _get_sii_rectification_amounts(self, origin):
        self.ensure_one()
        base = abs(origin.amount_untaxed_signed)
        if self.move_type == "in_refund":
            quota = origin._get_sii_in_taxes()[1]
        else:
            quota = abs(origin.amount_total_signed - origin.amount_untaxed_signed)
        return base, quota

    def _check_sii_substitution_refund(self):
        """Substitution with a non-zero rectified amount identifies the origin."""
        self.ensure_one()
        origin = self.reversed_entry_id
        if not origin:
            raise UserError(
                self.env._("A substitution refund needs the original invoice.")
            )
        base, quota = self._get_sii_rectification_amounts(origin)
        if not base and not quota:
            return
        number = self._get_sii_rectified_serial_number(origin)
        if not number or not origin.invoice_date:
            raise UserError(
                self.env._(
                    "A substitution refund with a rectified base or quota "
                    "needs the original invoice number and date."
                )
            )

    def _get_sii_refund_values(self):
        """TipoRectificativa and, for a substitution, the replaced amounts.

        FacturasRectificadas is optional on a received invoice. It is sent
        when the substitution reports a non-zero rectified base or quota.
        """
        self.ensure_one()
        values = {}
        if self.move_type not in ("in_refund", "out_refund"):
            return values
        if not self.sii_refund_type:
            return values
        values["TipoRectificativa"] = self.sii_refund_type
        if self.sii_refund_type != "S":
            return values
        origin = self.reversed_entry_id
        if not origin:
            return values
        base, quota = self._get_sii_rectification_amounts(origin)
        values["ImporteRectificacion"] = {
            "BaseRectificada": base,
            "CuotaRectificada": quota,
        }
        number = self._get_sii_rectified_serial_number(origin)
        if (base or quota) and number and origin.invoice_date:
            values["FacturasRectificadas"] = {
                "IDFacturaRectificada": [
                    {
                        "NumSerieFacturaEmisor": number,
                        "FechaExpedicionFacturaEmisor": self._change_date_format(
                            origin.invoice_date
                        ),
                    }
                ]
            }
        return values

    def _apply_open_later_deduction(self, origin):
        """Defer the refund to the origin period when that period is still open.

        The refund liquidation stays its own registration period. The net
        of both documents is then deducted in the origin's later period.
        A period that is no longer later than this refund is left empty:
        the adjustment is registered in the reception period.
        """
        self.ensure_one()
        if self.move_type != "in_refund" or not origin:
            return
        if (
            not origin.sii_deducir_en_periodo_posterior
            or not origin.sii_ejercicio_deduccion
            or not origin.sii_periodo_deduccion
        ):
            return
        if not self._is_sii_deduction_choice_allowed(
            origin.sii_ejercicio_deduccion, origin.sii_periodo_deduccion
        ):
            return
        self.write(
            {
                "sii_deducir_en_periodo_posterior": True,
                "sii_ejercicio_deduccion": origin.sii_ejercicio_deduccion,
                "sii_periodo_deduccion": origin.sii_periodo_deduccion,
            }
        )

    def _get_sii_invoice_type(self):
        self.ensure_one()
        invoice_type = ""
        if self.sii_lc_operation:
            return "LC"
        if self.move_type in ["in_invoice", "in_refund"]:
            if self.move_type == "in_refund":
                invoice_type = self.sii_refund_specific_invoice_type or "R4"
            elif self.sii_is_accounting_receipt:
                invoice_type = "F6"
            else:
                invoice_type = "F1"
        elif self.move_type in ["out_invoice", "out_refund"]:
            is_simplified = self._is_aeat_simplified_invoice()
            invoice_type = "F2" if is_simplified else "F1"
            if self.move_type == "out_refund":
                if self.sii_refund_specific_invoice_type:
                    invoice_type = self.sii_refund_specific_invoice_type
                else:
                    invoice_type = "R5" if is_simplified else "R1"
        return invoice_type

    def _get_aeat_invoice_dict_out(self, cancel=False):
        inv_dict = super()._get_aeat_invoice_dict_out(cancel=cancel)
        if cancel:
            return inv_dict
        if self.thirdparty_invoice:
            inv_dict["FacturaExpedida"]["EmitidaPorTercerosODestinatario"] = "S"
        if self.sii_registration_key_additional1:
            inv_dict["FacturaExpedida"].update(
                {
                    "ClaveRegimenEspecialOTrascendenciaAdicional1": (
                        self.sii_registration_key_additional1.code
                    )
                }
            )
        if self.sii_registration_key_additional2:
            inv_dict["FacturaExpedida"].update(
                {
                    "ClaveRegimenEspecialOTrascendenciaAdicional2": (
                        self.sii_registration_key_additional2.code
                    )
                }
            )
        self._adapt_sii_export_registration_keys(inv_dict["FacturaExpedida"])
        if self.sii_registration_key.code in ["12", "13"]:
            inv_dict["FacturaExpedida"]["DatosInmueble"] = {
                "DetalleInmueble": {
                    "SituacionInmueble": self.sii_property_location,
                    "ReferenciaCatastral": (self.sii_property_cadastrial_code or ""),
                }
            }
        exp_dict = inv_dict["FacturaExpedida"]
        if self.move_type == "out_refund":
            exp_dict.update(self._get_sii_refund_values())
        return inv_dict

    def _get_document_date(self):
        return self.invoice_date

    def _get_document_fiscal_date(self):
        return self.date

    def _get_document_serial_number(self):
        serial_number = (self.name or "")[0:60]
        if self.thirdparty_invoice:
            serial_number = self.thirdparty_number[0:60]
        return serial_number

    def _get_aeat_invoice_dict_in(self, cancel=False):
        """Build dict with data to send to AEAT WS for invoice types:
        in_invoice and in_refund.

        :param cancel: It indicates if the dictionary if for sending a
          cancellation of the invoice.
        :return: invoices (dict) : Dict XML with data for this invoice.
        """
        self.ensure_one()
        invoice_date = self._change_date_format(self.invoice_date)
        reg_date = self._change_date_format(self._get_account_registration_date())
        ejercicio, periodo = self._get_sii_incoming_liquidation_period(cancel=cancel)
        partner = self._aeat_get_partner()
        desglose_factura, tax_amount, not_in_amount_total = self._get_sii_in_taxes()
        inv_dict = {
            "IDFactura": {
                "IDEmisorFactura": {},
                "NumSerieFacturaEmisor": ((self.ref or "")[:60]),
                "FechaExpedicionFacturaEmisor": invoice_date,
            },
            "PeriodoLiquidacion": {"Ejercicio": ejercicio, "Periodo": periodo},
        }
        # Uso condicional de IDOtro/NIF
        ident = self._get_sii_identifier()
        inv_dict["IDFactura"]["IDEmisorFactura"].update(ident)
        if cancel:
            inv_dict["IDFactura"]["IDEmisorFactura"].update(
                {"NombreRazon": partner.name[0:120]}
            )
        else:
            invoice_type = self._get_sii_invoice_type()
            company_name = partner.name[0:120]
            if self.sii_dua_invoice:
                company_name = self.company_id.name
                if not self.sii_lc_operation:
                    invoice_type = "F5"

            inv_dict["FacturaRecibida"] = {
                "TipoFactura": invoice_type,
                "ClaveRegimenEspecialOTrascendencia": self.sii_registration_key.code,
                "DescripcionOperacion": self.sii_description,
                "DesgloseFactura": desglose_factura,
                "Contraparte": {"NombreRazon": company_name},
                "FechaRegContable": reg_date,
                "CuotaDeducible": tax_amount,
            }
            deduction_period_values = self._get_sii_deduction_period_values()
            if deduction_period_values:
                inv_dict["FacturaRecibida"].update(deduction_period_values)
            if not self.sii_dua_invoice:
                inv_dict["FacturaRecibida"]["ImporteTotal"] = (
                    -self.amount_total_signed - not_in_amount_total
                )
            if self.sii_macrodata:
                inv_dict["FacturaRecibida"].update(Macrodato="S")
            if self.sii_registration_key_additional1:
                inv_dict["FacturaRecibida"].update(
                    {
                        "ClaveRegimenEspecialOTrascendenciaAdicional1": (
                            self.sii_registration_key_additional1.code
                        )
                    }
                )
            if self.sii_registration_key_additional2:
                inv_dict["FacturaRecibida"].update(
                    {
                        "ClaveRegimenEspecialOTrascendenciaAdicional2": (
                            self.sii_registration_key_additional2.code
                        )
                    }
                )
            # Uso condicional de IDOtro/NIF
            inv_dict["FacturaRecibida"]["Contraparte"].update(ident)
            if self.move_type == "in_refund":
                inv_dict["FacturaRecibida"].update(self._get_sii_refund_values())

            if self.sii_dua_invoice:
                inv_dict["FacturaRecibida"].pop("FechaOperacion", None)
                nif = self.company_id.partner_id._parse_aeat_vat_info()[2]
                inv_dict["FacturaRecibida"]["IDEmisorFactura"] = {"NIF": nif}
                inv_dict["IDFactura"]["IDEmisorFactura"] = {"NIF": nif}
                inv_dict["FacturaRecibida"]["Contraparte"]["NIF"] = nif
        return inv_dict

    def _get_cancel_sii_invoice_dict(self):
        self.ensure_one()
        self._aeat_check_exceptions()
        if self.move_type in ["out_invoice", "out_refund"]:
            return self._get_aeat_invoice_dict_out(cancel=True)
        elif self.move_type in ["in_invoice", "in_refund"]:
            return self._get_aeat_invoice_dict_in(cancel=True)
        return {}

    def _sii_invoice_dict_not_modified(self):
        self.ensure_one()
        to_send = self._get_aeat_invoice_dict()
        content_sent = json.loads(self.aeat_content_sent)
        return to_send == content_sent

    def _post(self, soft=True):
        res = super()._post(soft=soft)
        for invoice in self.filtered(lambda x: x.sii_enabled and x.is_invoice()):
            invoice._aeat_check_exceptions()
            if (
                invoice.aeat_state in ["sent_modified", "sent"]
                and invoice._sii_invoice_dict_not_modified()
            ):
                if invoice.aeat_state == "sent_modified":
                    invoice.aeat_state = "sent"
                continue
            if invoice.aeat_state == "sent":
                invoice.aeat_state = "sent_modified"
            elif invoice.aeat_state == "cancelled":
                invoice.aeat_state = "cancelled_modified"
            company = invoice.company_id
            if company.sii_method != "auto":
                continue
            invoice._process_sii_send()
        return res

    def process_send_sii(self):
        return {
            "name": "Confirmation message for sending invoices to the SII",
            "type": "ir.actions.act_window",
            "view_mode": "form",
            "res_model": "wizard.send.sii",
            "views": [(False, "form")],
            "target": "new",
            "context": self.env.context,
        }

    @api.model
    def _get_valid_document_states(self):
        return SII_VALID_INVOICE_STATES

    def _cancel_invoice_to_sii(self, with_commit=False):
        self._cancel_document_to_sii(with_commit=with_commit)

    def cancel_sii(self):
        invoices = self.filtered(
            lambda i: (
                i.sii_enabled
                and i.state in ["cancel"]
                and i.aeat_state in ["sent", "sent_w_errors", "sent_modified"]
            )
        )
        if not invoices._cancel_send_to_sii():
            raise UserError(
                self.env._(
                    "You can not communicate the cancellation of this invoice "
                    "at this moment. Please, try again later."
                )
            )
        for invoice in invoices:
            company = invoice.company_id
            sii_sending_time = company._get_sii_sending_time()
            invoice.write({"sii_send_date": sii_sending_time, "sii_needs_cancel": True})
            # Create trigger if any company needs to send doc to SII now
            # so the sending to SII cron is executed as soon as possible
            if invoices.company_id.filtered(
                lambda company: company.send_mode == "auto"
                or (company.send_mode == "delayed" and company.delay_time == 0.0)
            ):
                sii_send_cron = self.env.ref("l10n_es_aeat_sii_oca.invoice_send_to_sii")
                self.env["ir.cron.trigger"].sudo().create(
                    {"cron_id": sii_send_cron.id, "call_at": fields.Datetime.now()}
                )

    def button_cancel(self):
        if not self._cancel_send_to_sii():
            raise UserError(
                self.env._("You cannot cancel this invoice. Please, try again later.")
            )
        res = super().button_cancel()
        for invoice in self.filtered(lambda x: x.sii_enabled):
            if invoice.aeat_state == "sent":
                invoice.aeat_state = "sent_modified"
            elif invoice.aeat_state == "cancelled_modified":
                # Case when repoen a cancelled invoice, validate and cancel
                # again without any SII communication.
                invoice.aeat_state = "cancelled"
        return res

    def button_draft(self):
        if not self._cancel_send_to_sii():
            raise UserError(
                self.env._(
                    "You can not set to draft this invoice because"
                    " the SII trigger could not be cancelled."
                )
            )
        return super().button_draft()

    def _get_document_product_exempt(self, applied_taxes):
        return set(
            self.mapped("invoice_line_ids")
            .filtered(
                lambda x: (
                    any(tax in x.tax_ids for tax in applied_taxes)
                    and x.product_id.sii_exempt_cause
                    and x.product_id.sii_exempt_cause != "none"
                )
            )
            .mapped("product_id.sii_exempt_cause")
        )

    def is_sii_invoice(self):
        """Hook method to be overridden in additional modules to verify
        if the invoice must be sended trough SII system, for special cases.

        :param self: Single invoice record
        :return: bool value indicating if the invoice should be sent to SII.
        """
        self.ensure_one()

    @api.depends(
        "invoice_line_ids",
        "invoice_line_ids.name",
        "company_id",
    )
    def _compute_sii_description(self):
        default_description = self.default_get(["sii_description"])["sii_description"]
        for invoice in self:
            description = ""
            if invoice.move_type in ["out_invoice", "out_refund"]:
                description = invoice.company_id.sii_header_customer or ""
            elif invoice.move_type in ["in_invoice", "in_refund"]:
                description = invoice.company_id.sii_header_supplier or ""
            method = invoice.company_id.sii_description_method
            if method == "fixed":
                description = (
                    description + invoice.company_id.sii_description
                ) or default_description
            elif method == "manual":
                if invoice.sii_description != default_description:
                    # keep current content if not default
                    description = invoice.sii_description
            else:  # auto method
                if invoice.invoice_line_ids:
                    if description:
                        description += " | "
                    names = invoice.mapped("invoice_line_ids.name") or invoice.mapped(
                        "invoice_line_ids.ref"
                    )
                    names = [unidecode(x) for x in names if x]  # Avoid "ugly" chars
                    description += " - ".join(filter(None, names)).replace("\n", " ")
            invoice.sii_description = description or "/"  # shrink to 500 by the field

    @api.depends(
        "company_id",
        "company_id.sii_enabled",
        "company_id.sii_start_date",
        "journal_id",
        "journal_id.sii_enabled",
        "move_type",
        "fiscal_position_id",
        "fiscal_position_id.aeat_active",
        "date",
        "invoice_line_ids",
    )
    def _compute_sii_enabled(self):
        """Compute if the invoice is enabled for the SII"""
        for invoice in self:
            dua_taxes = invoice._get_dua_sii_exempt_taxes()
            if (
                invoice.company_id.sii_enabled
                and invoice.journal_id.sii_enabled
                and invoice.is_invoice()
            ):
                invoice.sii_enabled = (
                    (
                        (
                            invoice.fiscal_position_id
                            and invoice.fiscal_position_id.aeat_active
                        )
                        or not invoice.fiscal_position_id
                    )
                    and (
                        not dua_taxes
                        or not invoice.invoice_line_ids.filtered(
                            lambda x, dua_taxes=dua_taxes: bool(dua_taxes & x.tax_ids)
                        )
                    )
                    and (
                        not invoice.company_id.sii_start_date
                        or not invoice.date
                        or invoice.date >= invoice.company_id.sii_start_date
                    )
                )
            else:
                invoice.sii_enabled = False

    @api.model
    def _search_sii_enabled(self, operator, value):
        domain = super()._search_sii_enabled(operator, value)
        invoice_types = self.get_sale_types() + self.get_purchase_types()
        condition_1 = [("journal_id.sii_enabled", operator, value)]
        condition_2 = [("fiscal_position_id.aeat_active", operator, value)]
        search_ko = (operator == "=" and not value) or (operator == "!=" and value)
        exp_condition = OR if search_ko else AND
        condition_3 = []
        if not search_ko:
            condition_2 = OR([condition_2, [("fiscal_position_id", "=", False)]])
            for company in self.env.companies.filtered("sii_enabled"):
                if company.sii_start_date:
                    condition_3.append(
                        [
                            ("company_id", "=", company.id),
                            ("date", ">=", company.sii_start_date),
                        ]
                    )
                else:
                    condition_3.append([("company_id", "=", company.id)])
            if condition_3:
                condition_3 = OR(condition_3)
        conditions = [domain, condition_1, condition_2]
        if condition_3:
            conditions.append(condition_3)
        return AND([[("move_type", "in", invoice_types)], exp_condition(conditions)])

    def _reverse_moves(self, default_values_list=None, cancel=False):
        # OVERRIDE
        if not default_values_list:
            default_values_list = [{} for move in self]
        for move, default_values in zip(self, default_values_list, strict=False):
            if move.sii_enabled:
                extra_dict = {}
                sii_refund_type = self.env.context.get("sii_refund_type", False)
                supplier_invoice_number_refund = move.env.context.get(
                    "supplier_invoice_number", False
                )
                if sii_refund_type:
                    extra_dict["sii_refund_type"] = sii_refund_type
                specific_type = self.env.context.get("sii_refund_specific_invoice_type")
                if specific_type:
                    extra_dict["sii_refund_specific_invoice_type"] = specific_type
                if supplier_invoice_number_refund:
                    extra_dict["ref"] = supplier_invoice_number_refund
                if extra_dict:
                    default_values.update(extra_dict)
        res = super()._reverse_moves(
            default_values_list=default_values_list,
            cancel=cancel,
        )
        for refund, origin in zip(res, self, strict=False):
            refund._apply_open_later_deduction(origin)
        return res

    def cancel_one_invoice(self, with_commit=False):
        self.sudo()._cancel_invoice_to_sii(with_commit=with_commit)

    @api.model
    def _write_sii_results(self, docs_vals):
        """Only SII fields are written, so skip the lines synchronization and the
        balance check done on each move write."""
        return super()._write_sii_results(
            {
                document.with_context(
                    check_move_validity=False, skip_invoice_sync=True
                ): doc_vals
                for document, doc_vals in docs_vals.items()
            }
        )

    @api.model
    def _send_to_sii_valid(self):
        remaining_documents = self.env["account.move"]
        documents = all_documents = self.search(
            [
                ("state", "in", self._get_valid_document_states()),
                (
                    "aeat_state",
                    "not in",
                    ["sent", "cancelled"],
                ),
                ("sii_send_date", "<=", fields.Datetime.now()),
            ]
        )
        if not documents:
            return remaining_documents
        batch = self._get_sii_batch()
        documents = all_documents[:batch]
        remaining_documents = all_documents - documents
        documents.confirm_one_document(with_commit=True)
        return remaining_documents

    @api.model
    def _send_to_sii_cancel(self):
        remaining_cancel_documents = self.env["account.move"]
        cancel_documents = all_cancel_documents = self.search(
            [
                ("state", "in", ["cancel"]),
                (
                    "aeat_state",
                    "in",
                    ["sent", "sent_w_errors", "sent_modified"],
                ),
                ("sii_needs_cancel", "=", True),
                ("sii_send_date", "<=", fields.Datetime.now()),
            ]
        )
        if cancel_documents:
            batch = self._get_sii_batch()
            cancel_documents = all_cancel_documents[:batch]
            remaining_cancel_documents = all_cancel_documents - cancel_documents
            cancel_documents.cancel_one_invoice(with_commit=True)
        return remaining_cancel_documents

    @api.model
    def _send_to_sii(self):
        remaining_documents = self._send_to_sii_valid()
        remaining_cancel_documents = self._send_to_sii_cancel()
        # Manage remaining invoices
        if remaining_documents or remaining_cancel_documents:
            sii_send_cron = self.env.ref("l10n_es_aeat_sii_oca.invoice_send_to_sii")
            self.env["ir.cron.trigger"].sudo().create(
                {"cron_id": sii_send_cron.id, "call_at": fields.Datetime.now()}
            )

    def _get_sii_tax_agency(self):
        if not self.journal_id.tax_agency_id:
            return super()._get_sii_tax_agency()
        return self.journal_id.tax_agency_id
