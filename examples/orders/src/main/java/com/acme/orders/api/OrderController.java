package com.acme.orders.api;

import com.acme.orders.application.PlaceOrder;
import com.acme.payments.api.PaymentsClient;

@RestController
public class OrderController {
    @PostMapping("/orders")
    public Order place() { return new PlaceOrder().run(); }

    @GetMapping("/orders/{orderId}")
    public Order read(String orderId) { return null; }
}
