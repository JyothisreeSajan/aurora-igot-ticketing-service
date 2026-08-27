import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("python-dotenv not installed, assuming env vars are set or parsing manually...")
    if os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    key, val = line.strip().split("=", 1)
                    os.environ[key] = val.strip("'\"")

api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("❌ Error: GEMINI_API_KEY or GOOGLE_API_KEY not found in .env")
    exit(1)

print(f"✅ API Key found (starts with {api_key[:5]}...)")

# Try with google-generativeai
try:
    import google.generativeai as genai
    print("\n[google-generativeai SDK] Attempting to connect...")
    genai.configure(api_key=api_key)
    
    models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    print("✅ Successfully authenticated! Available models:")
    for m in models:
        if 'gemini' in m.lower():
            print(f"  - {m}")
            
    print("\n[google-generativeai SDK] Testing text generation...")
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content("tell me a fact")
    print(f"✅ Response: {response.text.strip()}")
    exit(0)
except ImportError:
    print("ℹ️ google-generativeai not installed, trying langchain-google-genai...")
except Exception as e:
    print(f"❌ Error with google-generativeai: {e}")
    exit(1)

# Try with langchain-google-genai
try:
    from langchain_google_genai import ChatGoogleGenerativeAI
    print("\n[langchain-google-genai] Attempting to connect...")
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=api_key)
    print("✅ Successfully configured LangChain LLM.")
    
    print("\n[langchain-google-genai] Testing text generation...")
    response = llm.invoke("tell me a fact")
    print(f"✅ Response: {response.content.strip()}")
    exit(0)
except ImportError:
    print("ℹ️ langchain-google-genai not installed either.")
    print("❌ Please install a Gemini SDK (e.g., `pip install google-generativeai`) to run this test.")
    exit(1)
except Exception as e:
    print(f"❌ Error with langchain-google-genai: {e}")
    exit(1)
