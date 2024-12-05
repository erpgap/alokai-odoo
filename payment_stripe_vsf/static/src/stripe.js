/** @odoo-module **/
/* global Stripe */

import publicWidget from "@web/legacy/js/public/public_widget";

publicWidget.registry.WebsiteSaleStripe = publicWidget.Widget.extend({
    selector: '#stripe_checkout',
    events: {
        'submit form#payment-form': '_onClickCheckoutSubmit',
    },
    init: function () {
        let self = this;
        this._super.apply(this, arguments);
        const stripe = Stripe('pk_test_51Q5TQCCsRfUCvfWIlYOylZI5rSJ0FoqZGBFSFQ9rdDGmr6ZXTjKk038fWG7YPCDHzVlQsP5fAPp7Frxs2bFMVacC00CSEYMmw3', {
            'apiVersion': '2019-05-16',  // The API version of Stripe implemented in this module.
        });
        const clientSecret = 'pi_3QSeE9CsRfUCvfWI0j98szu1_secret_xlApILlvMl5RNgPPMjEl6gLxv';
        const billingDetails = {
            "name": "Test02",
            "email": "test02@hotmail.com",
            "phone": "966222333",
            "address": {
                "line1": "Rua Nossa Senhora de Fatima",
                "line2": "",
                "city": "Viseu",
                "state": "18",
                "country": "PT",
                "postal_code": "3510-605"
            }
        }
        const return_url = `http://localhost:8069/payment/stripe/return?reference=S00113`;
        this.stripeJs = stripe;
        this.clientSecret = clientSecret;
        this.billingDetails = billingDetails;
        this.return_url = return_url;

        let elementsOptions =  {
            appearance: { theme: 'stripe' },
            currency: "eur",
            captureMethod: "automatic",
            mode: 'payment',
            amount:9741,
            paymentMethodTypes: ['card', 'klarna', 'paypal']
        };
        this.paymentType = '';
        const paymentElementOptions = {
            defaultValues: {
                billingDetails: this.billingDetails
            }
        };
        this.elements = this.stripeJs.elements({ clientSecret , elementsOptions });
        const paymentElement = this.elements.create('payment', paymentElementOptions);
        paymentElement.mount('#payment-element');
        paymentElement.on('change', function(event) {
            if (event.complete) {
                self.paymentType = event.value.type;
            }
        });

        // Express Checkout Element
        /*const stripe = Stripe('pk_test_51Q5TQCCsRfUCvfWIlYOylZI5rSJ0FoqZGBFSFQ9rdDGmr6ZXTjKk038fWG7YPCDHzVlQsP5fAPp7Frxs2bFMVacC00CSEYMmw3', {
            'apiVersion': '2019-05-16',  // The API version of Stripe implemented in this module.
        });*/
        const expressCheckoutOptions = {
            clientSecret: this.clientSecret,
            billingDetails: this.billingDetails,
        };
        const expressElements = stripe.elements({ clientSecret , elementsOptions });
        const expressCheckoutElement = expressElements.create('expressCheckout', expressCheckoutOptions);
        expressCheckoutElement.mount('#express-checkout-element');

        expressCheckoutElement.on('click', function(event) {
            const options = {
                emailRequired: true
            };
            event.resolve(options);
        });

        expressCheckoutElement.on('confirm', function(event) {
            console.log('Express Checkout Confirm Triggered');

            stripe.createPaymentMethod({
                type: event.expressPaymentType,  // Example: 'paypal', 'card', etc.
                billing_details: billingDetails,
            }).then(function(result) {
                if (result.error) {
                    console.error("'Payment Method' Error:", result.error.message);
                } else {
                    const paymentMethod = result.paymentMethod.id; // id -> pm_....
                    console.log("'Payment Method' Created:", paymentMethod);

                    // Confirm Payment
                    stripe.confirmPayment({
                        elements: expressElements,
                        clientSecret: clientSecret,
                        confirmParams: {
                            payment_method: paymentMethod,
                            return_url: return_url,
                        },
                    }).then(function(result) {
                        if (result.error) {
                            console.error("'Payment' Error:", result.error.message);
                        } else if (result.paymentIntent && result.paymentIntent.status === 'succeeded') {
                            console.log("'Payment' Done with Success:", result.paymentIntent);
                            window.location.href = return_url;
                        } else {
                            console.log("'Payment' Failed or is Pendent", result.paymentIntent);
                        }
                    });
                }
            }).catch(function(error) {
                console.error("'Payment Method' Failed during Creation or Confirmation:", error);
            });
        });

    },
    async _onClickCheckoutSubmit(event) {
        event.preventDefault();
        const self = this;
        let elements = this.elements;
        if (this.paymentType === 'affirm') {
            return this.stripeJs.confirmAffirmPayment(this.clientSecret,
                {
                    payment_method: {
                    billing_details: self.billingDetails
                },
                // Return URL where the customer should be redirected after the authorization.
                return_url: self.return_url
            });
        } else if (this.paymentType === 'klarna') {
            return this.stripeJs.confirmKlarnaPayment(this.clientSecret,
                {
                    payment_method: {
                    billing_details: self.billingDetails
                },
                // Return URL where the customer should be redirected after the authorization.
                return_url: self.return_url
            });
        } else if (this.paymentType === 'paypal') {
            return this.stripeJs.confirmPayPalPayment(this.clientSecret,
                {
                    payment_method: {
                    billing_details: self.billingDetails
                },
                // Return URL where the customer should be redirected after the authorization.
                return_url: self.return_url
            });
        } else {
            return this.stripeJs.confirmPayment({
                elements,
                confirmParams: {
                    return_url: self.return_url
                },
            });
        }

        // if (error) {
        //     console.error(error);
        //     alert(error);
        // } else if (paymentIntent && paymentIntent.status === "succeeded") {
        //     console.log("Payment succeeded");
        //     alert(paymentIntent);
        // } else {
        //     console.log("Payment failed");
        //     alert("Payment failed");
        // }
    },
})
