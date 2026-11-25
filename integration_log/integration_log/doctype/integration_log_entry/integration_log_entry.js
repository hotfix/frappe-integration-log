frappe.ui.form.on("Integration Log Entry", {
	refresh(frm) {
		if (frm.is_new()) return;

		const call = (method, args) =>
			frappe.call({
				method: "integration_log.api." + method,
				args: { names: [frm.doc.name], ...args },
				freeze: true,
				callback: () => frm.reload_doc(),
			});

		if (frm.doc.state === "Open") {
			frm.add_custom_button(__("Acknowledge"), () => {
				frappe.prompt(
					{ fieldname: "note", fieldtype: "Small Text", label: __("Note") },
					(values) => call("acknowledge", { note: values.note || null }),
					__("Acknowledge"),
					__("Acknowledge")
				);
			});
		} else {
			frm.add_custom_button(__("Reopen"), () => call("reopen"));
		}

		if (frm.doc.source_doctype && frm.doc.source_name) {
			frm.add_custom_button(__("Open Source"), () =>
				frappe.set_route("Form", frm.doc.source_doctype, frm.doc.source_name)
			);
		}

		if (frm.doc.ref_doctype && frm.doc.ref_name) {
			frm.add_custom_button(__("Open Linked Document"), () =>
				frappe.set_route("Form", frm.doc.ref_doctype, frm.doc.ref_name)
			);
		}

		if (frm.doc.__onload && frm.doc.__onload.can_retry) {
			frm.add_custom_button(__("Retry"), () => {
				frappe.call({
					method: "integration_log.api.retry",
					args: { name: frm.doc.name },
					freeze: true,
					callback(r) {
						const result = r.message || {};
						frappe.show_alert({
							message: result.message,
							indicator: result.ok ? "green" : "red",
						});
						frm.reload_doc();
					},
				});
			});
		}
	},
});
