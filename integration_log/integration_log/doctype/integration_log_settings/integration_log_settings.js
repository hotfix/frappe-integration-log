frappe.ui.form.on("Integration Log Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Backfill now"), () => {
			frappe.call({
				method: "integration_log.api.start_backfill",
				freeze: true,
				callback: () =>
					frappe.show_alert({
						message: __("Backfill started in the background."),
						indicator: "green",
					}),
			});
		});
	},
});
