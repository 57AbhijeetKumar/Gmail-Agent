# app.py
import streamlit as st
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from typing import TypedDict
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from email.mime.text import MIMEText
import base64
import os

# -------------------- ENV --------------------
load_dotenv()

# -------------------- CONFIG --------------------
ALLOWED_DOMAINS = {"iands.com", "kogo.ai"}
SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
os.makedirs("tokens", exist_ok=True)

# -------------------- STREAMLIT CONFIG --------------------
st.set_page_config(page_title="AI Gmail Draft Generator", page_icon="")
st.title("AI Gmail Draft Generator (Human Approval Enabled)")

# -------------------- LLM --------------------
llm = ChatOpenAI()

class StructuredEmail(BaseModel):
    gmail_content: str = Field(description="Email body")
    gmail_subject: str = Field(description="Email subject")

structured_llm = llm.with_structured_output(StructuredEmail)

# -------------------- STATE --------------------
class GmailState(TypedDict):
    gmail_desc: str
    gmail_content: str
    gmail_subject: str

# -------------------- LANGGRAPH NODE --------------------
def create_gmail(state: GmailState):
    prompt = f"""
You are an expert professional email writer.

Email description:
{state['gmail_desc']}

Rules:
- Plain text only
- Proper paragraphs
- Professional tone
- No subject inside body
- End with sign-off

Return structured output only.
"""
    response = structured_llm.invoke(prompt)
    return {
        "gmail_content": response.gmail_content,
        "gmail_subject": response.gmail_subject,
    }

# -------------------- LANGGRAPH FLOW --------------------
graph = StateGraph(GmailState)
graph.add_node("create_gmail", create_gmail)
graph.add_edge(START, "create_gmail")
graph.add_edge("create_gmail", END)
workflow = graph.compile()

# -------------------- HELPERS --------------------
def is_allowed_user(email: str) -> bool:
    return email.split("@")[-1].lower() in ALLOWED_DOMAINS

def parse_emails(input_str: str) -> list:
    """Split comma-separated emails and remove extra spaces"""
    return [email.strip() for email in input_str.split(",") if email.strip()]

# -------------------- GMAIL AUTH --------------------
def get_gmail_service():
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(port=0)
    service = build("gmail", "v1", credentials=creds)

    profile = service.users().getProfile(userId="me").execute()
    user_email = profile["emailAddress"]

    if not is_allowed_user(user_email):
        raise PermissionError("Access denied: unauthorized domain")

    with open(f"tokens/token_{user_email}.json", "w") as f:
        f.write(creds.to_json())

    return service, user_email

# -------------------- GMAIL ACTIONS --------------------
def create_gmail_draft(service, to_list, subject, body, cc_list=None):
    msg = MIMEText(body.replace("\n", "<br>"), "html")
    msg["to"] = ", ".join(to_list)
    msg["subject"] = subject
    if cc_list:
        msg["cc"] = ", ".join(cc_list)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = service.users().drafts().create(
        userId="me",
        body={"message": {"raw": raw}}
    ).execute()
    return draft.get("id")


def send_gmail_message(service, to_list, subject, body, cc_list=None, draft_id=None):
    msg = MIMEText(body.replace("\n", "<br>"), "html")
    msg["to"] = ", ".join(to_list)
    msg["subject"] = subject
    if cc_list:
        msg["cc"] = ", ".join(cc_list)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(
        userId="me",
        body={"raw": raw}
    ).execute()

    if draft_id:
        service.users().drafts().delete(userId="me", id=draft_id).execute()

# -------------------- SESSION STATE --------------------
for key in [
    "user_service", "user_email",
    "generated_email", "generated_subject",
    "generated_to", "generated_cc", "draft_id"
]:
    if key not in st.session_state:
        st.session_state[key] = None

# -------------------- AUTH UI --------------------
st.subheader("Gmail Authentication")

if not st.session_state.user_service:
    if st.button("Authenticate Gmail"):
        try:
            service, email = get_gmail_service()
            st.session_state.user_service = service
            st.session_state.user_email = email
            st.success(f"Logged in as {email}")
        except Exception as e:
            st.error(str(e))
else:
    st.info(f"Logged in as {st.session_state.user_email}")
    if st.button("Logout"):
        st.session_state.user_service = None
        st.session_state.user_email = None
        st.stop()

# -------------------- EMAIL GENERATION --------------------
if st.session_state.user_service:
    st.subheader("Generate Email")

    user_prompt = st.text_area("Email description")
    to_email = st.text_input("To (comma-separated for multiple recipients)")
    cc_email = st.text_input("CC (Optional, comma-separated)")

    if st.button("Generate & Create Draft"):
        response = workflow.invoke({"gmail_desc": user_prompt})

        st.session_state.generated_email = response["gmail_content"]
        st.session_state.generated_subject = response["gmail_subject"]
        st.session_state.generated_to = parse_emails(to_email)
        st.session_state.generated_cc = parse_emails(cc_email) if cc_email else None

        st.session_state.draft_id = create_gmail_draft(
            st.session_state.user_service,
            st.session_state.generated_to,
            response["gmail_subject"],
            response["gmail_content"],
            cc_list=st.session_state.generated_cc
        )

        st.success("Draft created. Please review below.")

# -------------------- HUMAN APPROVAL --------------------
if st.session_state.generated_email:
    st.subheader("Review Before Sending")

    st.text_input("To", ", ".join(st.session_state.generated_to), disabled=True)
    st.text_input("CC", ", ".join(st.session_state.generated_cc) if st.session_state.generated_cc else "", disabled=True)
    st.text_input("Subject", st.session_state.generated_subject, disabled=True)
    st.text_area(
        "Email Content",
        st.session_state.generated_email,
        height=300,
        disabled=True
    )

    col1, col2 = st.columns(2)

    def reset_fields():
        st.session_state.generated_email = None
        st.session_state.generated_subject = None
        st.session_state.generated_to = None
        st.session_state.generated_cc = None
        st.session_state.draft_id = None

    with col1:
        if st.button("Cancel"):
            reset_fields()
            st.warning("Email cancelled")

    with col2:
        if st.button("Send Email"):
            send_gmail_message(
                st.session_state.user_service,
                st.session_state.generated_to,
                st.session_state.generated_subject,
                st.session_state.generated_email,
                cc_list=st.session_state.generated_cc,
                draft_id=st.session_state.draft_id
            )
            st.success("Email sent successfully!")
            reset_fields()