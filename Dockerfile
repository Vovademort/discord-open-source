FROM openjdk:17-jdk-slim

# рабочая папка
WORKDIR /app

# копируем код
COPY . /app

# собираем проект
RUN ./gradlew build -x test

# запускаем FredBoat
CMD ["java", "-jar", "FredBoat-Launcher/build/libs/FredBoat-Launcher.jar"]
