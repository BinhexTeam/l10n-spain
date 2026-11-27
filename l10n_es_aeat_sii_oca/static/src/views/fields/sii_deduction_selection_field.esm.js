/* License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl). */

/** @odoo-module **/

import {registry} from "@web/core/registry";
import {
    SelectionField,
    selectionField,
} from "@web/views/fields/selection/selection_field";

export class SiiDeductionSelectionField extends SelectionField {
    get options() {
        const options = super.options;
        const bounds = this.props.record.data.sii_deduction_period_bounds;
        if (!bounds) {
            return options;
        }
        if (this.props.name === "sii_ejercicio_deduccion") {
            return options.filter((option) => bounds.years.includes(String(option[0])));
        }
        if (this.props.name === "sii_periodo_deduccion") {
            const periods = this.allowedPeriods(bounds);
            return options.filter((option) => periods.includes(option[0]));
        }
        return options;
    }

    /**
     * @override
     * Keep the label of a value that the filter has removed.
     */
    get string() {
        if (this.type !== "selection") {
            return super.string;
        }
        const value = this.props.record.data[this.props.name];
        if (!value) {
            return "";
        }
        const option = super.options.find((item) => item[0] === value);
        return option ? option[1] : value;
    }

    allowedPeriods(bounds) {
        const year = this.props.record.data.sii_ejercicio_deduccion;
        if (String(year) === String(bounds.liquidation_year)) {
            return bounds.periods_same_year;
        }
        return bounds.periods_other_year;
    }
}

export const siiDeductionSelectionField = {
    ...selectionField,
    component: SiiDeductionSelectionField,
};

registry.category("fields").add("sii_deduction_selection", siiDeductionSelectionField);
