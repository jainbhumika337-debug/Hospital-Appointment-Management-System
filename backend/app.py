
from flask import Flask, request, jsonify
from flask_cors import CORS
import oracledb
from datetime import date, datetime
import re
from dotenv import load_dotenv
import os

app = Flask(__name__)
CORS(app)

# Load database credentials securely from the project .env file
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_DSN = os.getenv("DB_DSN")


def get_connection():
    return oracledb.connect(
        user=DB_USER,
        password=DB_PASSWORD,
        dsn=DB_DSN
    )


def rows_to_dicts(cursor, rows):
    columns = [col[0].lower() for col in cursor.description]
    return [
        {
            key: (
                value.isoformat()
                if isinstance(value, (datetime, date))
                else value
            )
            for key, value in zip(columns, row)
        }
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
    connection = None
    try:
        connection = get_connection()
        return jsonify({
            "status": "success",
            "message": "Flask and Oracle Database connected."
        })
    except Exception as e:
        return error_response(
            "Database connection failed: " + str(e), 500
        )
    finally:
        if connection:
            connection.close()


@app.route("/api/departments", methods=["GET"])
def get_departments():
    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("""
            SELECT * FROM departments
            ORDER BY department_id
        """)
        return jsonify(rows_to_dicts(cursor, cursor.fetchall()))
    except Exception as e:
        return error_response(str(e), 500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.route("/api/doctors", methods=["GET"])
def get_doctors():
    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("""
            SELECT * FROM doctors
            ORDER BY doctor_id
        """)
        return jsonify(rows_to_dicts(cursor, cursor.fetchall()))
    except Exception as e:
        return error_response(str(e), 500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.route("/api/patients", methods=["GET"])
def get_patients():
    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("""
            SELECT * FROM patients
            ORDER BY patient_id
        """)
        return jsonify(rows_to_dicts(cursor, cursor.fetchall()))
    except Exception as e:
        return error_response(str(e), 500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()



@app.route("/api/appointments", methods=["GET"])
def get_appointments():
    connection = None
    cursor = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                a.appointment_id,
                a.patient_id,
                p.patient_name AS patient_name,
                a.doctor_id,
                d.doctor_name AS doctor_name,
                a.appointment_date,
                a.appointment_time,
                a.status,
                a.reason,
                a.created_at
            FROM appointments a
            JOIN patients p
                ON p.patient_id = a.patient_id
            JOIN doctors d
                ON d.doctor_id = a.doctor_id
            ORDER BY a.appointment_id DESC
        """)

        return jsonify(rows_to_dicts(cursor, cursor.fetchall()))

    except Exception as e:
        return error_response(str(e), 500)

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.route("/api/appointments", methods=["POST"])
def book_appointment():
    connection = None
    cursor = None

    try:
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return error_response("Valid JSON object is required.")

        required_fields = [
            "patient_id",
            "doctor_id",
            "appointment_date",
            "appointment_time"
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

            if not re.fullmatch(
                r"(?:[01]\d|2[0-3]):[0-5]\d",
                appointment_time
            ):
                return error_response(
                    "Appointment time must be in HH:MM format."
                )

        except (ValueError, TypeError):
            return error_response(
                "Invalid patient ID, doctor ID, date or time."
            )

        reason = str(data.get("reason") or "").strip()

        connection = get_connection()
        cursor = connection.cursor()

        appointment_id_var = cursor.var(oracledb.NUMBER)

        cursor.callproc(
            "BOOK_APPOINTMENT",
            [
                patient_id,
                doctor_id,
                appointment_date,
                appointment_time,
                reason,
                appointment_id_var
            ]
        )

        connection.commit()

        return jsonify({
            "status": "success",
            "message": "Appointment booked successfully.",
            "appointment_id": int(appointment_id_var.getvalue())
        }), 201

    except Exception as e:
        if connection:
            connection.rollback()

        error_text = str(e)

        if "20004" in error_text:
            message = (
                "This doctor slot is already booked. "
                "Choose another time."
            )
        elif "20001" in error_text:
            message = "Appointment date cannot be in the past."
        elif "20002" in error_text:
            message = "Patient not found."
        elif "20003" in error_text:
            message = "Doctor not found."
        else:
            message = error_text

        return error_response(message, 400)

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.route(
    "/api/appointments/<int:appointment_id>/cancel",
    methods=["PATCH"]
)
def cancel_appointment(appointment_id):
    connection = None
    cursor = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.callproc(
            "CANCEL_APPOINTMENT",
            [appointment_id]
        )

        connection.commit()

        return jsonify({
            "status": "success",
            "message": "Appointment cancelled successfully."
        })

    except Exception as e:
        if connection:
            connection.rollback()

        error_text = str(e)

        if "20007" in error_text:
            message = (
                "Appointment cannot be cancelled because "
                "it is already cancelled or completed."
            )
        else:
            message = error_text

        return error_response(message, 400)

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.route(
    "/api/appointments/<int:appointment_id>/complete",
    methods=["PATCH"]
)
def complete_appointment(appointment_id):
    connection = None
    cursor = None

    try:
        connection = get_connection()
        cursor = connection.cursor()

        cursor.callproc(
            "COMPLETE_APPOINTMENT",
            [appointment_id]
        )

        connection.commit()

        return jsonify({
            "status": "success",
            "message": "Appointment completed successfully."
        })

    except Exception as e:
        if connection:
            connection.rollback()

        error_text = str(e)

        if "20008" in error_text:
            message = "Cancelled appointment cannot be completed."
        elif "20009" in error_text:
            message = "Appointment is already completed."
        elif "20010" in error_text or "NO_DATA_FOUND" in error_text:
            message = "Appointment not found."
        else:
            message = error_text

        return error_response(message, 400)

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
