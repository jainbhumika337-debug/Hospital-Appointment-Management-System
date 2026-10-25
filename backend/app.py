
from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg
from psycopg.rows import dict_row
from datetime import date, datetime
import re
from dotenv import load_dotenv
import os

app = Flask(__name__)
CORS(app)

# Load environment variables from the project root .env file.
load_dotenv(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
)

DATABASE_URL = os.getenv("DATABASE_URL")


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is missing. Configure it in Render or your local .env."
        )

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=15,
        sslmode="require"
    )


def json_safe(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def rows_to_dicts(rows):
    return [
        {key: json_safe(value) for key, value in row.items()}
        for row in rows
    ]


def error_response(message, status_code=400):
    return jsonify({
        "status": "error",
        "message": message
    }), status_code


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "success",
        "message": "Hospital Management API is running."
    })


@app.route("/api/health", methods=["GET"])
def health():
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 AS connected")
                cursor.fetchone()

        return jsonify({
            "status": "success",
            "message": "Flask and Neon PostgreSQL connected."
        })
    except Exception:
        app.logger.exception("Database health check failed")
        return error_response("Database connection failed. Check server configuration.", 500)


@app.route("/api/departments", methods=["GET"])
def get_departments():
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT department_id, department_name, description
                    FROM departments
                    ORDER BY department_id
                """)
                rows = cursor.fetchall()

        return jsonify(rows_to_dicts(rows))
    except Exception:
        app.logger.exception("Could not load departments")
        return error_response("Could not load departments.", 500)


@app.route("/api/doctors", methods=["GET"])
def get_doctors():
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT doctor_id, doctor_name, specialization, phone,
                           email, consultation_fee, department_id
                    FROM doctors
                    ORDER BY doctor_id
                """)
                rows = cursor.fetchall()

        return jsonify(rows_to_dicts(rows))
    except Exception:
        app.logger.exception("Could not load doctors")
        return error_response("Could not load doctors.", 500)


@app.route("/api/patients", methods=["GET"])
def get_patients():
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT patient_id, patient_name, date_of_birth, gender,
                           phone, email, address, created_at
                    FROM patients
                    ORDER BY patient_id
                """)
                rows = cursor.fetchall()

        return jsonify(rows_to_dicts(rows))
    except Exception:
        app.logger.exception("Could not load patients")
        return error_response("Could not load patients.", 500)


@app.route("/api/appointments", methods=["GET"])
def get_appointments():
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        a.appointment_id,
                        a.patient_id,
                        p.patient_name,
                        a.doctor_id,
                        d.doctor_name,
                        a.appointment_date,
                        a.appointment_time,
                        a.status,
                        a.reason,
                        a.created_at
                    FROM appointments a
                    JOIN patients p ON p.patient_id = a.patient_id
                    JOIN doctors d ON d.doctor_id = a.doctor_id
                    ORDER BY a.appointment_id DESC
                """)
                rows = cursor.fetchall()

        return jsonify(rows_to_dicts(rows))
    except Exception:
        app.logger.exception("Could not load appointments")
        return error_response("Could not load appointments.", 500)


@app.route("/api/appointments", methods=["POST"])
def book_appointment():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return error_response("Valid JSON object is required.")

    required_fields = [
        "patient_id", "doctor_id",
        "appointment_date", "appointment_time"
    ]

    for field in required_fields:
        if data.get(field) in (None, ""):
            return error_response(f"{field} is required.")

    try:
        patient_id = int(data["patient_id"])
        doctor_id = int(data["doctor_id"])

        if patient_id <= 0 or doctor_id <= 0:
            return error_response(
                "Patient ID and doctor ID must be positive."
            )

        appointment_date = date.fromisoformat(
            str(data["appointment_date"])
        )

        if appointment_date < date.today():
            return error_response(
                "Appointment date cannot be in the past."
            )

        appointment_time = str(data["appointment_time"])
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", appointment_time):
            return error_response(
                "Appointment time must be in HH:MM format."
            )

    except (ValueError, TypeError):
        return error_response("Invalid patient ID, doctor ID, date or time.")

    reason = str(data.get("reason") or "").strip()

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                # Validate referenced records.
                cursor.execute(
                    "SELECT 1 FROM patients WHERE patient_id = %s",
                    (patient_id,)
                )
                if cursor.fetchone() is None:
                    return error_response("Patient not found.", 404)

                cursor.execute(
                    "SELECT 1 FROM doctors WHERE doctor_id = %s",
                    (doctor_id,)
                )
                if cursor.fetchone() is None:
                    return error_response("Doctor not found.", 404)

                # Serialize booking checks for the same doctor and date.
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (doctor_id, appointment_date.toordinal())
                )

                cursor.execute("""
                    SELECT 1
                    FROM appointments
                    WHERE doctor_id = %s
                      AND appointment_date = %s
                      AND appointment_time = %s
                      AND UPPER(status) NOT IN ('CANCELLED', 'CANCELED')
                    LIMIT 1
                """, (doctor_id, appointment_date, appointment_time))

                if cursor.fetchone() is not None:
                    return error_response(
                        "This doctor slot is already booked. Choose another time.",
                        409
                    )

                cursor.execute("""
                    INSERT INTO appointments (
                        patient_id, doctor_id, appointment_date,
                        appointment_time, status, reason
                    )
                    VALUES (%s, %s, %s, %s, 'BOOKED', %s)
                    RETURNING appointment_id
                """, (
                    patient_id, doctor_id, appointment_date,
                    appointment_time, reason or None
                ))

                appointment_id = cursor.fetchone()["appointment_id"]

        return jsonify({
            "status": "success",
            "message": "Appointment booked successfully.",
            "appointment_id": appointment_id
        }), 201

    except Exception:
        app.logger.exception("Could not book appointment")
        return error_response("Could not book appointment. Check server logs.", 500)


@app.route("/api/appointments/<int:appointment_id>/cancel", methods=["PATCH"])
def cancel_appointment(appointment_id):
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT status
                    FROM appointments
                    WHERE appointment_id = %s
                    FOR UPDATE
                """, (appointment_id,))
                row = cursor.fetchone()

                if row is None:
                    return error_response("Appointment not found.", 404)

                status = str(row["status"]).upper()
                if status in ("CANCELLED", "CANCELED", "COMPLETED"):
                    return error_response(
                        "Appointment cannot be cancelled because it is already cancelled or completed.",
                        409
                    )

                cursor.execute("""
                    UPDATE appointments
                    SET status = 'CANCELLED'
                    WHERE appointment_id = %s
                """, (appointment_id,))

        return jsonify({
            "status": "success",
            "message": "Appointment cancelled successfully."
        })

    except Exception:
        app.logger.exception("Could not cancel appointment")
        return error_response("Could not cancel appointment. Check server logs.", 500)


@app.route("/api/appointments/<int:appointment_id>/complete", methods=["PATCH"])
def complete_appointment(appointment_id):
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT status
                    FROM appointments
                    WHERE appointment_id = %s
                    FOR UPDATE
                """, (appointment_id,))
                row = cursor.fetchone()

                if row is None:
                    return error_response("Appointment not found.", 404)

                status = str(row["status"]).upper()
                if status in ("CANCELLED", "CANCELED"):
                    return error_response(
                        "Cancelled appointment cannot be completed.",
                        409
                    )
                if status == "COMPLETED":
                    return error_response(
                        "Appointment is already completed.",
                        409
                    )

                cursor.execute("""
                    UPDATE appointments
                    SET status = 'COMPLETED'
                    WHERE appointment_id = %s
                """, (appointment_id,))

        return jsonify({
            "status": "success",
            "message": "Appointment completed successfully."
        })

    except Exception:
        app.logger.exception("Could not complete appointment")
        return error_response("Could not complete appointment. Check server logs.", 500)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
