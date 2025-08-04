#include <Adafruit_ADS1X15.h>
#include <LiquidCrystal_I2C.h>
#include <Wire.h>

// Pin definitions
int button1Pin = 2;
int button2Pin = 3;
int ledPin = 6;
const int IN1 = 9;
const int IN2 = 4;
const int IN3 = 7;
const int IN4 = 8;

// Address definitions
#define ADS_ADDR 0x48
#define LCD_ADDR 0x27
int photodiodePin = 0;

// Communication
const int HANDSHAKE = 0;
const int VOLTAGE_REQUEST = 1;
const int ON_REQUEST = 2;
const int STREAM = 3;
const int READ_DAQ_DELAY = 4;
int daqMode = ON_REQUEST;
int daqDelay = 100;
String daqDelayStr;
unsigned long timeOfLastDAQ = 0;
int inByte;

// Geometry of LCD
const int nRows = 2;
const int nCols = 16;

// Logical Values
int maxPhotoRaw = 75;
int minPhotoRaw = -1;
float absorbanceBlank = -0.104;
float photodiodeEmptyNormalized = 0.83;
int currMotorTick = 0;
int elevatorUp = true;
int elevatorRiseTicks = 800;
volatile bool waitingForRelease = false;
volatile bool ejectRequested = false;
unsigned long pressStartTime = 0;
unsigned long buttonHoldMillis = 800;
float absorbanceScaling = 0.96;
float absorbanceOffset = -0.01;


// Stepper sequence 
const int steps[8][4] = {
  {1, 0, 0, 0},
  {1, 1, 0, 0},
  {0, 1, 0, 0},
  {0, 1, 1, 0},
  {0, 0, 1, 0},
  {0, 0, 1, 1},
  {0, 0, 0, 1},
  {1, 0, 0, 1}
};

// Object instantiation
Adafruit_ADS1015 ads;
LiquidCrystal_I2C lcd = LiquidCrystal_I2C(LCD_ADDR, nCols, nRows);

// Method declaration
void stepMotor(int stepsToMove, int delayMs = 1);
float sample(bool standalone);
void cbEject();
void cbRecord();
void calibration();
float measureLight();
unsigned long sendData();
float getAbsorbance(float measurement);

void setup() {
  // Set I2C to be fast mode
  Wire.setClock(400000);

  // Pin initialization
  pinMode(button1Pin, INPUT_PULLUP);
  pinMode(button2Pin, INPUT_PULLUP);
  pinMode(ledPin, OUTPUT);
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);

  // Process initialization
  Serial.begin(115200);
  ads.begin(ADS_ADDR);
  ads.setGain(GAIN_TWOTHIRDS);
  lcd.init();
  lcd.backlight();

  // Label message on LCD
  lcd.setCursor(0, 0);
  lcd.print("Awaiting Input.");

  // Add interrupts
  attachInterrupt(digitalPinToInterrupt(button1Pin), cbEject, FALLING);
  attachInterrupt(digitalPinToInterrupt(button2Pin), cbRecord, FALLING);

  // Turn LED on
  digitalWrite(ledPin, HIGH);

}

void loop() {

  // If we're streaming
  if (daqMode == STREAM) {
    if (millis() - timeOfLastDAQ >= daqDelay) {
      timeOfLastDAQ = sendData();
    }
  }

  // COMMUNICATION
  if (Serial.available() > 0) {
    // Read in request
    int inByte = Serial.read();

    // If data is requested, fetch it and write it, or handshake
    switch(inByte) {
      case VOLTAGE_REQUEST:
        timeOfLastDAQ = sendData();
        break;
      case ON_REQUEST:
        daqMode = ON_REQUEST;
        break;
      case STREAM:
        daqMode = STREAM;
        break;
      case READ_DAQ_DELAY:
        // Read in delay, knowing it is appended with an x
        daqDelayStr = Serial.readStringUntil('x');

        // Convert to int and store
        daqDelay = daqDelayStr.toInt();

        break;
      case HANDSHAKE:
        if (Serial.availableForWrite()) {
          Serial.println("Message received.");
        }
        break;
    }
  }
  
  // Button held/pressed check
  if (waitingForRelease && digitalRead(button2Pin) == HIGH) {
    unsigned long pressDuration = millis() - pressStartTime;

    if (pressDuration >= buttonHoldMillis) { // Long press
      calibration();
    } else { // Short press
      sample(true);
    }

    waitingForRelease = false;
    attachInterrupt(digitalPinToInterrupt(button2Pin), cbRecord, FALLING);
  }

  // Handle eject
  if (ejectRequested) {
    if(elevatorUp) {
      stepMotor(elevatorRiseTicks);
      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("Ejecting...");
    }
    else {
      stepMotor(-elevatorRiseTicks);
      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("Sample inserted.");
    }

    elevatorUp = not elevatorUp;
    ejectRequested = false;
  } 

}

float sample(bool standalone) {
  float measurement = measureLight();
  float absorbance = getAbsorbance(measurement);

  lcd.clear();
  if(standalone) {
    lcd.setCursor(3, 0);
    lcd.print("Absorbance: "); 
    lcd.setCursor(6, 1);
    lcd.print(absorbance, 3);
  }
  else {
    lcd.setCursor(0, 0);
    lcd.print("Sending Data...");
  }
  return absorbance;
}

void stepMotor(int stepsToMove, int delayMs = 1) {
  int direction = (stepsToMove > 0) ? 1 : -1;
  stepsToMove = abs(stepsToMove);

  int stepIndex = 0;
  for (int i = 0; i < stepsToMove; i++) {
    stepIndex += direction;
    stepIndex = (stepIndex + 8) % 8;  // wrap between 0–7

    digitalWrite(IN1, steps[stepIndex][0]);
    digitalWrite(IN2, steps[stepIndex][1]);
    digitalWrite(IN3, steps[stepIndex][2]);
    digitalWrite(IN4, steps[stepIndex][3]);

    delay(delayMs);
  }
}

void cbEject() {
  static unsigned long lastInterruptTime = 0;
  unsigned long interruptTime = millis();

  if (interruptTime - lastInterruptTime > 200) {
    ejectRequested = true;
  }

  lastInterruptTime = interruptTime;
}
  
void cbRecord() {
  static unsigned long lastInterruptTime = 0;
  unsigned long interruptTime = millis();

  if (interruptTime - lastInterruptTime > 200) {
    detachInterrupt(digitalPinToInterrupt(button2Pin));
    pressStartTime = millis();
    waitingForRelease = true;
  }

  lastInterruptTime = interruptTime;
}

void calibration() {
  absorbanceBlank = 0.0; // reset absorbance blank
  absorbanceBlank = getAbsorbance(measureLight());
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Calibration");
  lcd.setCursor(0, 1);
  lcd.print("Successful.");
}

float measureLight() {
  // Gather photodiode values and map    
  float photodiodeRaw = ads.readADC_SingleEnded(0);
  int photodiodeMapped = map((int)photodiodeRaw, minPhotoRaw, maxPhotoRaw, 0, maxPhotoRaw);
  float photodiodeNormalized = (float)photodiodeMapped / maxPhotoRaw;
  return photodiodeNormalized;
}

unsigned long sendData() {
  // Read value from analog pin
  float absorbance = sample(false);

  // Get the time point
  unsigned long time_ms = millis();

  // Write the result
  if (Serial.availableForWrite()) {
    String outstr = String(String(time_ms, DEC) + "," + String(absorbance, 3));
    Serial.println(outstr);
  }

  return time_ms;
}

float getAbsorbance(float measurement) {
  if (measurement < 0.0001) measurement = 0.0001;
  return absorbanceScaling * (log10(photodiodeEmptyNormalized / measurement) - absorbanceBlank) + absorbanceOffset;
}
