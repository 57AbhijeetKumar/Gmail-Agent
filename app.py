import streamlit as st
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from typing import TypedDict
from dotenv import load_dotenv

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
st.title("AI Gmail Draft Generator (Restricted Access)")

# -------------------- LLM --------------------
llm = ChatOpenAI()

# -------------------- STATE --------------------
class GmailState(TypedDict):
    gmail: str

# -------------------- LANGGRAPH NODE --------------------
def create_gmail(state: GmailState):
    prompt = state["gmail"]
    response = llm.invoke(prompt).content
    return {"gmail": response}

# -------------------- LANGGRAPH FLOW --------------------
graph = StateGraph(GmailState)
graph.add_node("create_gmail", create_gmail)
graph.add_edge(START, "create_gmail")
graph.add_edge("create_gmail", END)
workflow = graph.compile()

# -------------------- HELPERS --------------------
def is_allowed_user(email: str) -> bool:
    domain = email.split("@")[-1].lower()
    return domain in ALLOWED_DOMAINS

# -------------------- GMAIL AUTH --------------------
def get_gmail_service():
    """Authenticate user with Gmail and return service and user email"""
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(port=0)

    service = build("gmail", "v1", credentials=creds)

    profile = service.users().getProfile(userId="me").execute()
    user_email = profile["emailAddress"]

    # DOMAIN RESTRICTION
    if not is_allowed_user(user_email):
        raise PermissionError(
            f"Access denied for {user_email}. "
            "Only iands.com and kogo.ai users are allowed."
        )

    token_file = f"tokens/token_{user_email}.json"
    with open(token_file, "w") as f:
        f.write(creds.to_json())

    return service, user_email

def load_gmail_service(user_email: str):
    token_file = f"tokens/token_{user_email}.json"
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        service = build("gmail", "v1", credentials=creds)
        return service
    return None

# -------------------- CREATE DRAFT --------------------
def create_gmail_draft(service, to, subject, body):
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    draft = service.users().drafts().create(
        userId="me",
        body={"message": {"raw": raw}}
    ).execute()
    return draft

# -------------------- SESSION STATE --------------------
if "user_service" not in st.session_state:
    st.session_state.user_service = None

if "user_email" not in st.session_state:
    st.session_state.user_email = None

# -------------------- AUTH UI --------------------
st.subheader("🔑 Gmail Account Management")

if st.session_state.user_service is None:
    if st.button("Authenticate Gmail"):
        try:
            service, user_email = get_gmail_service()
            st.session_state.user_service = service
            st.session_state.user_email = user_email
            st.success(f"Authenticated as {user_email}")
        except Exception as e:
            st.error(f"Authentication failed: {e}")
else:
    st.info(f"Logged in as: {st.session_state.user_email}")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Switch Account"):
            st.session_state.user_service = None
            st.session_state.user_email = None
            st.stop()

    with col2:
        if st.button("Logout"):
            token_file = f"tokens/token_{st.session_state.user_email}.json"
            if os.path.exists(token_file):
                os.remove(token_file)
            st.session_state.user_service = None
            st.session_state.user_email = None
            st.success("Logged out successfully")
            st.stop()

# -------------------- HARD UI BLOCK (EXTRA SAFETY) --------------------
if st.session_state.user_email and not is_allowed_user(st.session_state.user_email):
    st.error("You are not authorized to use this application.")
    st.stop()

# -------------------- EMAIL GENERATION --------------------
if st.session_state.user_service:
    st.subheader("Enter Email Prompt")

    user_prompt = st.text_area(
        "Describe the email you want to draft",
        placeholder="Create a professional email regarding amount deduction from my SBI bank account",
        height=120
    )

    to_email = st.text_input("To", value="customercare@sbi.co.in")
    subject = st.text_input(
        "Subject",
        value="Regarding Unauthorized Amount Deduction"
    )

    if st.button("Generate & Draft Email"):
        if not user_prompt.strip():
            st.warning("Please enter a prompt")
        else:
            with st.spinner("Generating email and creating Gmail draft..."):
                try:
                    response = workflow.invoke({"gmail": user_prompt})
                    email_body = response["gmail"]

                    create_gmail_draft(
                        service=st.session_state.user_service,
                        to=to_email,
                        subject=subject,
                        body=email_body
                    )

                    st.success("Gmail draft created successfully!")
                    st.subheader("Generated Email")
                    st.text_area(
                        "Email Content",
                        email_body,
                        height=300
                    )

                except Exception as e:
                    st.error(f"Error: {e}")
