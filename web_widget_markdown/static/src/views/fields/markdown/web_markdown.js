import { _t } from "@web/core/l10n/translation";
import { loadBundle } from "@web/core/assets";
import { registry } from "@web/core/registry";
import { useBus } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, onWillStart, useState, useEffect, useRef } from "@odoo/owl";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { formatText } from "@web/views/fields/formatters";
import { useOwnedDialogs } from "@web/core/utils/hooks";
import { TranslationDialog } from "@web/views/fields/translation_dialog";


export function useTranslationDialog() {
    const addDialog = useOwnedDialogs();

    async function openTranslationDialog({ record, fieldName }) {
        const saved = await record.save();
        if (!saved) {
            return;
        }
        const { resModel, resId } = record;

        addDialog(TranslationDialog, {
            fieldName: fieldName,
            resId: resId,
            resModel: resModel,
            userLanguageValue: record.data[fieldName] || "",
            isComingFromTranslationAlert: false,
            onSave: async () => {
                await record.load();
            },
        });
    }

    return openTranslationDialog;
}


export class MarkdownField extends Component {
    static template = "web_widget_markdown.FieldMarkdown";
    static props = {
        ...standardFieldProps,
        placeholder: { type: String, optional: true },
        readonly: { type: Boolean, optional: true },
    };
    static defaultProps = {
        readonly: false,
    };
    setup() {
        this.state = useState({});
        onWillStart(async () => await loadBundle("web_widget_markdown.easymdejs_lib"));
        useRecordObserver((record) => {
            this.state.initialValue = formatText(record.data[this.props.name]);
        });
        this.translationDialog = useTranslationDialog();

        this.isDirty = false;
        this.textareaRef = useRef("textarea");
        useEffect(
            (el) => {
                if (!el) {
                    return;
                }
                const toolbar = ["bold", "italic", "heading", "|", "quote", "unordered-list", "ordered-list", "|", "link", "image", "|", "preview", "side-by-side", "fullscreen"];
                if(this.isTranslatable) {
                    toolbar.push("|")
                    toolbar.push(
                        {
                            name: "translation",
                            action: (editor) => {
                                this.markdownTranslate();
                            },
                            className: "fa fa-globe",
                            title: _t("Translate"),
                        }
                    );
                }
                const easyConfig = {
                    toolbar: toolbar,
                    element: this.textareaRef.el,
                    initialValue: this.value,
                    uniqueId: "markdown-"+this.model+this.res_id,
                    autoRefresh: { delay: 500 },
                }
                if (this.nodeOptions) {
                    easyConfig = {...easyConfig, ...this.nodeOptions};
                }
                this.easymde = new EasyMDE(easyConfig);
                this.easymde.codemirror.on("change", () => {
                    this.handleChange(this.easymde.value());
                });
            },
            () => [this.textareaRef.el]
        );
        const { model } = this.props.record;
        useBus(model.bus, "NEED_LOCAL_CHANGES", ({ detail }) =>
            this.commitChanges()
        );
    }

    get isTranslatable() {
        return this.props.record.fields[this.props.name].translate;
    }

    handleChange(editedValue) {
        if (this.state.initialValue !== editedValue) {
            this.isDirty = true;
        } else {
            this.isDirty = false;
        }
        this.props.record.model.bus.trigger("FIELD_IS_DIRTY", this.isDirty);
        this.editedValue = editedValue;
    }

    markdownTranslate () {
        const fieldName = this.props.name;
        const record = this.props.record;
        this.translationDialog({ fieldName, record });
    }

    async commitChanges() {
        if (!this.props.readonly && this.isDirty) {
            if (this.state.initialValue !== this.editedValue) {
                await this.props.record.update({ [this.props.name]: this.editedValue });
            }
            this.isDirty = false;
            this.props.record.model.bus.trigger("FIELD_IS_DIRTY", false);
        }
    }
}

export const markdownField = {
    component: MarkdownField,
    supportedTypes: ["text"],
    extractProps: ({ attrs, options }) => ({
        placeholder: attrs.placeholder,
    }),
};

registry.category("fields").add("markdown", markdownField);