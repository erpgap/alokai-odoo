/** @odoo-module **/
/* global Stripe */

import publicWidget from "@web/legacy/js/public/public_widget";

publicWidget.registry.WebsiteSaleStripe = publicWidget.Widget.extend({
    selector: '#stripe-elements-container',

    start: function () {
        // initialize stripe (substitui pela tua publishable key)
        const stripe = Stripe('pk_test_51QKjOpCMyOnNwWBFSgilPbJVWL0aNyp1f1EuhFNLGycd0HDJyAVb9933sWamaqw87Xusg4XuSQvcRwTo3Bdm8IbR00EukoJarv');

        // container onde colocámos o data-attr no template
        const container = document.getElementById('stripe-elements-container');
        if (!container) { console.error('Container stripe-elements-container not found'); return; }

        // ler o JSON do data-attribute de forma segura
        const jsonStr = container.getAttribute('data-stripe-inline-form-values') || '{}';
        let inlineFormValues;
        try {
            inlineFormValues = JSON.parse(jsonStr);
        } catch (e) {
            console.error('Invalid inline_form_values JSON:', e, jsonStr);
            inlineFormValues = { payment_methods: [], billing_details: {}, amount: 0, currency: 'usd' };
        }

        const billingDetails = inlineFormValues.billing_details || {};
        const currency = inlineFormValues.currency || 'usd';
        const amount = inlineFormValues.amount || 0;

        this.paymentElements = {};
        this.activePaymentMethod = null;

        // cria um Stripe Elements separado por método e monta nos containers criados no QWeb
        inlineFormValues.payment_methods.forEach(method => {
            try {
                const elements = stripe.elements({
                    mode: 'payment',
                    currency: currency,
                    amount: amount,
                    paymentMethodTypes: [method],
                });

                const paymentElement = elements.create('payment', {
                    defaultValues: { billingDetails: billingDetails }
                });

                const mountDiv = document.getElementById('element_' + method);
                if (mountDiv) {
                    paymentElement.mount(mountDiv);
                    // inicialmente escondido — mostraremos quando o método for seleccionado
                    mountDiv.style.display = 'none';
                    this.paymentElements[method] = paymentElement;
                } else {
                    console.warn('Mount div not found for method:', method);
                }
            } catch (err) {
                // se Stripe não suportar esse método neste contexto, evita crash
                console.warn('Could not create element for', method, err && err.message ? err.message : err);
            }
        });

        // 🚀 Rollback/reset ao recarregar a página
        const radios = container.querySelectorAll('input[name="payment_method_radio"]');
        radios.forEach(radio => {
            radio.checked = false; // limpa qualquer seleção guardada
        });
        Object.keys(this.paymentElements).forEach(m => {
            const el = document.getElementById('element_' + m);
            if (el) el.style.display = 'none';
        });
        this.activePaymentMethod = null;

        radios.forEach(radio => {
            radio.addEventListener('change', (ev) => {
                if (ev.target.checked) {
                    this.activePaymentMethod = ev.target.value;
                    // mostra só o element activo
                    Object.keys(this.paymentElements).forEach(m => {
                        const el = document.getElementById('element_' + m);
                        if (el) el.style.display = (m === this.activePaymentMethod) ? 'block' : 'none';
                    });
                }
            });
            // permitir clique em label/card para activar radio (UX)
            const lbl = container.querySelector('label[for="' + radio.id + '"]');
            if (lbl) {
                lbl.addEventListener('click', () => {
                    radio.checked = true;
                    radio.dispatchEvent(new Event('change'));
                });
            }
        });

        // Pay button: executa o element correcto (aqui sem client_secret — POC)
        const payBtn = document.getElementById('pay-btn');
        payBtn.addEventListener('click', async () => {
            if (!this.activePaymentMethod) {
                alert('Escolhe um método de pagamento (um só).');
                return;
            }

            // Neste POC não temos client_secret: apenas mostramos qual seria invocado.
            // Em produção, chamarias confirmPayment/confirmAffirmPayment com o client_secret apropriado.
            console.log('Submitting payment method:', this.activePaymentMethod);
            alert('Aqui irias submeter o método: ' + this.activePaymentMethod);
        });
    }
});
